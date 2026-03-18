from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from .cache import RemediationCache
from .config import Settings
from .contracts import (
    AxeViolationPayload,
    FixReport,
    FixResult,
    PdfViolationPayload,
)
from .gradient_client import GradientInferenceClient
from .heuristics import apply_coder_heuristic
from .pdf_pipeline import scan_pdf_bytes
from .reporting import build_summary
from .routing import build_router_graph, load_routing_config
from .tracing import resolve_trace_decorator
from .web_guidance import enrich_web_result


def _normalize_class_attr(attr_text: str) -> str:
    value_match = re.search(r"""class\s*=\s*(["'])(.*?)\1""", attr_text, flags=re.IGNORECASE | re.DOTALL)
    if not value_match:
        return ' class=""'
    classes = re.split(r"\s+", value_match.group(2).strip())
    normalized = []
    for css_class in classes:
        css_class = re.sub(r"\d+", "#", css_class)
        css_class = re.sub(r"(duplicate|active|next|prev)", "variant", css_class)
        if css_class:
            normalized.append(css_class)
    return f' class="{" ".join(sorted(set(normalized)))}"'


@dataclass
class PdfJob:
    id: str
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    payload: PdfViolationPayload | None = None
    report: FixReport | None = None
    error: str | None = None


@dataclass
class WebJob:
    id: str
    url: str
    viewport: dict[str, int] | None = None
    status: str = "queued"
    stage: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    total_nodes: int = 0
    completed_nodes: int = 0
    report: FixReport | None = None
    error: str | None = None


class SonarA11yService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._verify_gradient_prerequisites()
        self.gradient_client = GradientInferenceClient(settings)
        self.remediation_cache = RemediationCache(
            path=settings.remediation_cache_path,
            enabled=settings.remediation_cache_enabled,
        )
        self.trace = resolve_trace_decorator()
        self.routing_config = load_routing_config()
        self.vision_keywords = tuple(
            str(keyword).strip().lower()
            for keyword in self.routing_config.get("vision_keywords", [])
            if str(keyword).strip()
        )
        self.router_graph = build_router_graph(
            gradient=self.gradient_client,
            trace=self.trace,
            routing_config=self.routing_config,
        )
        self.web_jobs: dict[str, WebJob] = {}
        self.pdf_jobs: dict[str, PdfJob] = {}
        self._jobs_lock = asyncio.Lock()

    def _verify_gradient_prerequisites(self) -> None:
        # Env validation already enforced in settings loader.
        if not self.settings.gradient_api_key.strip():
            raise RuntimeError("GRADIENT_API_KEY is required for startup.")

    @staticmethod
    def _compact_for_fingerprint(text: str, max_chars: int) -> str:
        compact = re.sub(r"\s+", " ", text or "").strip().lower()
        if len(compact) > max_chars:
            return compact[:max_chars]
        return compact

    def _node_fingerprint(self, rule_id: str, node: Any) -> str:
        raw_html = self._compact_for_fingerprint(getattr(node, "rawHtml", ""), 2400)
        failure = self._compact_for_fingerprint(getattr(node, "failureSummary", ""), 600)
        digest = hashlib.sha256(f"{rule_id.lower()}|{failure}|{raw_html}".encode("utf-8")).hexdigest()
        screenshot = getattr(node, "elementScreenshotBase64", None) or ""
        if screenshot:
            digest = hashlib.sha256(f"{digest}|{hashlib.sha256(screenshot.encode('utf-8')).hexdigest()}".encode("utf-8")).hexdigest()
        return digest

    def _node_pattern_fingerprint(self, rule_id: str, node: Any) -> str:
        raw_html = getattr(node, "rawHtml", "") or ""
        pattern_html = raw_html.lower()
        pattern_html = re.sub(r"<!--.*?-->", " ", pattern_html, flags=re.DOTALL)
        pattern_html = re.sub(r"""\b(?:href|src|data-[a-z0-9_-]+)\s*=\s*(?:"[^"]*"|'[^']*')""", ' data-ref=""', pattern_html)
        pattern_html = re.sub(r"""\b(?:id|title|aria-label|aria-labelledby)\s*=\s*(?:"[^"]*"|'[^']*')""", ' data-meta=""', pattern_html)
        pattern_html = re.sub(r"\bclass\s*=\s*(\"[^\"]*\"|'[^']*')", lambda m: _normalize_class_attr(m.group(0)), pattern_html)
        pattern_html = re.sub(r">\s*[^<]+\s*<", "><", pattern_html)
        pattern_html = re.sub(r"#[0-9a-f]{3,8}", "#color", pattern_html)
        pattern_html = re.sub(r"\d+", "#", pattern_html)
        pattern_html = re.sub(r"\s+", " ", pattern_html).strip()

        failure = getattr(node, "failureSummary", "") or ""
        pattern_failure = failure.lower()
        pattern_failure = re.sub(r"#[0-9a-f]{3,8}", "#color", pattern_failure)
        pattern_failure = re.sub(r"\d+(?:\.\d+)?", "#", pattern_failure)
        pattern_failure = re.sub(r"\s+", " ", pattern_failure).strip()

        return hashlib.sha256(
            f"{rule_id.lower()}|{pattern_failure[:400]}|{pattern_html[:1200]}".encode("utf-8")
        ).hexdigest()

    def _format_processing_error(self, exc: Exception) -> str:
        if isinstance(exc, asyncio.TimeoutError):
            return f"Node remediation timed out after {self.settings.web_node_timeout_seconds}s."
        message = str(exc).strip()
        if message:
            return message
        return f"Unhandled {exc.__class__.__name__} during node remediation."

    def _should_retry_processing_error(self, exc: Exception) -> bool:
        return isinstance(exc, asyncio.TimeoutError)

    def _is_vision_rule(self, rule_id: str) -> bool:
        lowered = (rule_id or "").lower()
        return any(keyword in lowered for keyword in self.vision_keywords)

    async def process_web_payload(
        self,
        payload: AxeViolationPayload,
        progress_callback: Callable[[int, int], Awaitable[None] | None] | None = None,
    ) -> FixReport:
        work_items: list[tuple[str, str, Any]] = []
        for violation in payload.violations:
            for node in violation.nodes:
                work_items.append((violation.ruleId, violation.impact, node))

        if self.settings.max_web_nodes > 0:
            work_items = work_items[: self.settings.max_web_nodes]

        semaphore = asyncio.Semaphore(max(1, self.settings.web_parallelism))
        progress_lock = asyncio.Lock()
        completed_count = 0
        total_items = len(work_items)
        results_by_index: list[FixResult | None] = [None] * total_items
        severities_by_index: list[str | None] = [None] * total_items

        groups_by_fingerprint: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for index, (rule_id, impact, node) in enumerate(work_items):
            fingerprint = self._node_fingerprint(rule_id, node)
            item = {
                "index": index,
                "rule_id": rule_id,
                "impact": impact,
                "node": node,
                "fingerprint": fingerprint,
            }
            groups_by_fingerprint[fingerprint].append(item)

        async def invoke_router(rule_id: str, node: Any) -> FixResult:
            state = {
                "rule_id": rule_id,
                "node": node,
            }
            async with semaphore:
                output = await asyncio.wait_for(
                    self.router_graph.ainvoke(state),
                    timeout=self.settings.web_node_timeout_seconds,
                )
            return output["result"]

        async def run_with_retry(factory: Callable[[], Awaitable[FixResult]]) -> FixResult:
            last_error: Exception | None = None
            for attempt in range(self.settings.web_node_max_retries + 1):
                try:
                    return await factory()
                except Exception as exc:
                    last_error = exc
                    if attempt >= self.settings.web_node_max_retries or not self._should_retry_processing_error(exc):
                        raise
                    await asyncio.sleep(max(0, self.settings.web_retry_backoff_seconds))
            if last_error:
                raise last_error
            raise RuntimeError("Unexpected retry state while processing node.")

        def build_processing_error(item: dict[str, Any], exc: Exception) -> FixResult:
            assigned_agent = "vision_node" if self._is_vision_rule(item["rule_id"]) else "coder_node"
            node = item["node"]
            return FixResult(
                ruleId=item["rule_id"],
                targetSelector=getattr(node, "targetSelector", None),
                assignedAgent=assigned_agent,
                status="error",
                rationale="Node processing failed before remediation output was returned.",
                traceId=f"error-{uuid.uuid4()}",
                modelId="N/A",
                latencyMs=0,
                error=self._format_processing_error(exc),
            )

        async def finalize_group(
            group_items: list[dict[str, Any]],
            representative_result: FixResult,
            *,
            from_cache: bool,
        ) -> None:
            nonlocal completed_count
            for position, item in enumerate(group_items):
                node = item["node"]
                if from_cache:
                    result = representative_result.model_copy(
                        update={
                            "targetSelector": getattr(node, "targetSelector", None),
                            "latencyMs": 0,
                            "tokenUsage": None,
                            "costUsd": None,
                            "rationale": "Reused remediation from persistent cache.",
                        }
                    )
                elif position == 0:
                    result = representative_result.model_copy(
                        update={"targetSelector": getattr(node, "targetSelector", None)}
                    )
                else:
                    result = representative_result.model_copy(
                        update={
                            "targetSelector": getattr(node, "targetSelector", None),
                            "latencyMs": 0,
                            "tokenUsage": None,
                            "costUsd": None,
                            "rationale": "Reused remediation from an equivalent duplicate finding.",
                        }
                    )

                results_by_index[item["index"]] = result
                severities_by_index[item["index"]] = item["impact"]

                if progress_callback:
                    async with progress_lock:
                        completed_count += 1
                        maybe_result = progress_callback(completed_count, total_items)
                        if asyncio.iscoroutine(maybe_result):
                            await maybe_result

        async def process_single_item(item: dict[str, Any]) -> FixResult:
            try:
                result = await run_with_retry(lambda: invoke_router(item["rule_id"], item["node"]))
                return enrich_web_result(item["rule_id"], item["node"], result)
            except Exception as exc:
                return enrich_web_result(item["rule_id"], item["node"], build_processing_error(item, exc))

        async def process_batch(batch_items: list[dict[str, Any]]) -> dict[str, FixResult]:
            if not batch_items:
                return {}

            rule_id = batch_items[0]["rule_id"]
            indexed_batch_items = [
                {
                    "index": offset,
                    "targetSelector": getattr(item["node"], "targetSelector", None),
                    "rawHtml": getattr(item["node"], "rawHtml", ""),
                    "failureSummary": getattr(item["node"], "failureSummary", ""),
                }
                for offset, item in enumerate(batch_items)
            ]

            try:
                async def invoke_batch() -> dict[str, FixResult]:
                    async with semaphore:
                        response = await asyncio.wait_for(
                            self.gradient_client.rewrite_html_batch(indexed_batch_items, rule_id),
                            timeout=self.settings.web_node_timeout_seconds,
                        )

                    corrected_map, batch_error = self.gradient_client.normalize_coder_batch_output(
                        response.text,
                        expected_count=len(batch_items),
                    )

                    results: dict[str, FixResult] = {}
                    for offset, item in enumerate(batch_items):
                        corrected_html = corrected_map.get(offset)
                        if corrected_html:
                            results[item["fingerprint"]] = enrich_web_result(
                                item["rule_id"],
                                item["node"],
                                FixResult(
                                ruleId=item["rule_id"],
                                targetSelector=getattr(item["node"], "targetSelector", None),
                                assignedAgent="coder_node",
                                status="success",
                                proposedHtml=corrected_html,
                                confidence=0.85,
                                rationale="Generated from failure summary and source HTML.",
                                traceId=response.trace_id,
                                modelId=response.model_id,
                                latencyMs=response.latency_ms,
                                tokenUsage=response.token_usage,
                                costUsd=response.cost_usd,
                                ),
                            )
                        else:
                            fallback_error = batch_error or "Batch remediation output did not include this item."
                            results[item["fingerprint"]] = enrich_web_result(
                                item["rule_id"],
                                item["node"],
                                FixResult(
                                ruleId=item["rule_id"],
                                targetSelector=getattr(item["node"], "targetSelector", None),
                                assignedAgent="coder_node",
                                status="error",
                                rationale="Coder model output could not be normalized into corrected HTML.",
                                traceId=response.trace_id,
                                modelId=response.model_id,
                                latencyMs=response.latency_ms,
                                tokenUsage=response.token_usage,
                                costUsd=response.cost_usd,
                                error=fallback_error,
                                ),
                            )
                    return results

                batch_results = await run_with_retry(invoke_batch)
            except Exception:
                batch_results = {}

            if batch_results:
                missing_fingerprints = {
                    item["fingerprint"]
                    for item in batch_items
                    if item["fingerprint"] not in batch_results or batch_results[item["fingerprint"]].status != "success"
                }
            else:
                missing_fingerprints = {item["fingerprint"] for item in batch_items}

            if missing_fingerprints:
                fallback_results = await asyncio.gather(
                    *(
                        process_single_item(item)
                        for item in batch_items
                        if item["fingerprint"] in missing_fingerprints
                    )
                )
                for item, fallback_result in zip(
                    (item for item in batch_items if item["fingerprint"] in missing_fingerprints),
                    fallback_results,
                    strict=False,
                ):
                    batch_results[item["fingerprint"]] = fallback_result

            return batch_results

        representative_items = [group[0] for group in groups_by_fingerprint.values()]
        coder_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        pending_tasks: list[asyncio.Task[tuple[dict[str, list[FixResult]], bool]]] = []

        for item in representative_items:
            cached_result = self.remediation_cache.get(item["fingerprint"])
            if cached_result is not None:
                cached_result = enrich_web_result(item["rule_id"], item["node"], cached_result)
                if cached_result.status == "success":
                    self.remediation_cache.put(item["fingerprint"], cached_result)
                await finalize_group(
                    groups_by_fingerprint[item["fingerprint"]],
                    cached_result,
                    from_cache=True,
                )
                continue

            heuristic_result = apply_coder_heuristic(item["rule_id"], item["node"])
            if heuristic_result is not None:
                heuristic_result = enrich_web_result(item["rule_id"], item["node"], heuristic_result)
                self.remediation_cache.put(item["fingerprint"], heuristic_result)
                await finalize_group(
                    groups_by_fingerprint[item["fingerprint"]],
                    heuristic_result,
                    from_cache=False,
                )
                continue

            if self._is_vision_rule(item["rule_id"]):
                async def run_vision(vision_item: dict[str, Any] = item) -> tuple[dict[str, list[dict[str, Any]]], bool]:
                    result = await process_single_item(vision_item)
                    return ({vision_item["fingerprint"]: [result]}, False)

                pending_tasks.append(asyncio.create_task(run_vision()))
            else:
                pattern_key = self._node_pattern_fingerprint(item["rule_id"], item["node"])
                coder_groups[(item["rule_id"], pattern_key)].append(item)

        batch_size = max(1, self.settings.web_batch_size)
        for group_items in coder_groups.values():
            for start in range(0, len(group_items), batch_size):
                batch_items = group_items[start : start + batch_size]

                async def run_coder_batch(
                    chunk: list[dict[str, Any]] = batch_items,
                ) -> tuple[dict[str, list[FixResult]], bool]:
                    results = await process_batch(chunk)
                    return ({fingerprint: [result] for fingerprint, result in results.items()}, True)

                pending_tasks.append(asyncio.create_task(run_coder_batch()))

        for task in asyncio.as_completed(pending_tasks):
            group_results, should_cache = await task
            for fingerprint, result_list in group_results.items():
                result = result_list[0]
                if should_cache and result.status == "success":
                    self.remediation_cache.put(fingerprint, result)
                await finalize_group(
                    groups_by_fingerprint[fingerprint],
                    result,
                    from_cache=False,
                )

        results = [result for result in results_by_index if result is not None]
        severities = [severity for severity in severities_by_index if severity is not None]

        summary = build_summary(results, severities)
        return FixReport(
            reportType="web",
            scanMetadata=payload.scanMetadata.model_dump(mode="json"),
            summary=summary,
            results=results,
        )

    def cache_stats(self) -> dict[str, int | str | bool]:
        return self.remediation_cache.stats()

    async def scan_then_process(self, url: str, viewport: dict[str, int] | None = None) -> FixReport:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.settings.phase1_scanner_url}/scan",
                json={"url": url, "viewport": viewport},
            )
            if response.status_code >= 400:
                detail = response.text.strip()
                raise RuntimeError(
                    f"Phase1 scanner failed with status {response.status_code}: {detail}"
                )
            payload = AxeViolationPayload.model_validate(response.json())
        return await self.process_web_payload(payload)

    async def create_web_job(self, url: str, viewport: dict[str, int] | None = None) -> str:
        job_id = str(uuid.uuid4())
        async with self._jobs_lock:
            self.web_jobs[job_id] = WebJob(id=job_id, url=url, viewport=viewport)

        async def run() -> None:
            job = self.web_jobs[job_id]
            job.status = "processing"
            job.stage = "scanning"
            job.updated_at = time.time()
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    response = await client.post(
                        f"{self.settings.phase1_scanner_url}/scan",
                        json={"url": url, "viewport": viewport},
                    )
                    if response.status_code >= 400:
                        detail = response.text.strip()
                        raise RuntimeError(
                            f"Phase1 scanner failed with status {response.status_code}: {detail}"
                        )
                    payload = AxeViolationPayload.model_validate(response.json())

                work_items = sum(len(v.nodes) for v in payload.violations)
                job.total_nodes = min(work_items, self.settings.max_web_nodes) if self.settings.max_web_nodes > 0 else work_items
                job.completed_nodes = 0
                job.stage = "remediating"
                job.updated_at = time.time()

                async def on_progress(completed: int, _total: int) -> None:
                    job.completed_nodes = completed
                    job.updated_at = time.time()

                job.report = await self.process_web_payload(payload, progress_callback=on_progress)
                job.completed_nodes = job.total_nodes
                job.status = "completed"
                job.stage = "completed"
                job.updated_at = time.time()
            except Exception as exc:
                job.status = "failed"
                job.stage = "failed"
                job.error = str(exc)
                job.updated_at = time.time()

        asyncio.create_task(run())
        return job_id

    async def create_pdf_job_from_bytes(self, data: bytes, source: str, filename: str | None = None) -> str:
        job_id = str(uuid.uuid4())
        async with self._jobs_lock:
            self.pdf_jobs[job_id] = PdfJob(id=job_id)

        async def run() -> None:
            job = self.pdf_jobs[job_id]
            job.status = "processing"
            job.updated_at = time.time()
            try:
                payload = await scan_pdf_bytes(
                    source=source,
                    filename=filename,
                    data=data,
                    gradient_client=self.gradient_client,
                )
                report = await self.process_pdf_payload(payload)
                job.payload = payload
                job.report = report
                job.status = "completed"
                job.updated_at = time.time()
            except Exception as exc:
                job.status = "failed"
                job.error = str(exc)
                job.updated_at = time.time()

        asyncio.create_task(run())
        return job_id

    async def create_pdf_job_from_url(self, pdf_url: str) -> str:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.get(pdf_url)
            response.raise_for_status()
            data = response.content
        return await self.create_pdf_job_from_bytes(data=data, source=pdf_url)

    async def process_pdf_payload(self, payload: PdfViolationPayload) -> FixReport:
        results: list[FixResult] = []
        severities: list[str] = []
        semaphore = asyncio.Semaphore(max(1, min(self.settings.web_parallelism, 4)))

        async def process_location(violation: Any, location: Any) -> tuple[FixResult, str]:
            async with semaphore:
                response = await self.gradient_client.analyze_pdf_page(
                    page_text_hint=location.evidence,
                    rule_context=f"Rule={violation.ruleId} Severity={violation.severity}",
                )
            summary, details, parse_error = self.gradient_client.normalize_pdf_output(response.text)
            return (
                FixResult(
                    ruleId=violation.ruleId,
                    page=location.page,
                    assignedAgent="pdf_node",
                    status="success" if summary else "error",
                    proposedHtml=None,
                    proposedAltText=summary,
                    confidence=location.ocrConfidence if location.ocrDerived else 0.8,
                    rationale=violation.description,
                    traceId=response.trace_id,
                    modelId=response.model_id,
                    latencyMs=response.latency_ms,
                    tokenUsage=response.token_usage,
                    costUsd=response.cost_usd,
                    error=parse_error,
                    details=details,
                ),
                violation.severity,
            )

        outcomes = await asyncio.gather(
            *(
                process_location(violation, location)
                for violation in payload.violations
                for location in violation.locations
            )
        )
        results = [result for result, _severity in outcomes]
        severities = [severity for _result, severity in outcomes]

        summary = build_summary(results, severities)
        return FixReport(
            reportType="pdf",
            scanMetadata=payload.scanMetadata.model_dump(mode="json"),
            summary=summary,
            results=results,
        )
