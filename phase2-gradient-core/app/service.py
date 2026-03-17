from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
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
from .pdf_pipeline import scan_pdf_bytes
from .reporting import build_summary
from .routing import build_router_graph, load_routing_config
from .tracing import resolve_trace_decorator


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
        self.router_graph = build_router_graph(
            gradient=self.gradient_client,
            trace=self.trace,
            routing_config=load_routing_config(),
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

    def _format_processing_error(self, exc: Exception) -> str:
        if isinstance(exc, asyncio.TimeoutError):
            return f"Node remediation timed out after {self.settings.web_node_timeout_seconds}s."
        message = str(exc).strip()
        if message:
            return message
        return f"Unhandled {exc.__class__.__name__} during node remediation."

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
        dedupe_lock = asyncio.Lock()
        dedupe_tasks: dict[str, asyncio.Task[FixResult]] = {}
        completed_count = 0

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

        async def process_one(rule_id: str, impact: str, node: Any) -> tuple[FixResult, str]:
            nonlocal completed_count
            fingerprint = self._node_fingerprint(rule_id, node)
            reused = False

            try:
                cached_result = self.remediation_cache.get(fingerprint)
                if cached_result is not None:
                    result = cached_result.model_copy(
                        update={
                            "targetSelector": getattr(node, "targetSelector", None),
                            "latencyMs": 0,
                            "tokenUsage": None,
                            "costUsd": None,
                            "rationale": "Reused remediation from persistent cache.",
                        }
                    )
                    return result, impact

                async with dedupe_lock:
                    task = dedupe_tasks.get(fingerprint)
                    if task is None:
                        task = asyncio.create_task(invoke_router(rule_id, node))
                        dedupe_tasks[fingerprint] = task
                    else:
                        reused = True

                result: FixResult = await task
                if reused:
                    result = result.model_copy(
                        update={
                            "targetSelector": getattr(node, "targetSelector", None),
                            "latencyMs": 0,
                            "tokenUsage": None,
                            "costUsd": None,
                            "rationale": "Reused remediation from an equivalent duplicate finding.",
                        }
                    )
                elif result.status == "success":
                    self.remediation_cache.put(fingerprint, result)
                return result, impact
            except Exception as exc:
                assigned_agent = "vision_node" if ("image" in rule_id or "alt" in rule_id) else "coder_node"
                return (
                    FixResult(
                        ruleId=rule_id,
                        targetSelector=getattr(node, "targetSelector", None),
                        assignedAgent=assigned_agent,
                        status="error",
                        rationale="Node processing failed before remediation output was returned.",
                        traceId=f"error-{uuid.uuid4()}",
                        modelId="N/A",
                        latencyMs=0,
                        error=self._format_processing_error(exc),
                    ),
                    impact,
                )
            finally:
                if progress_callback:
                    async with progress_lock:
                        completed_count += 1
                        maybe_result = progress_callback(completed_count, len(work_items))
                        if asyncio.iscoroutine(maybe_result):
                            await maybe_result

        outcomes = await asyncio.gather(
            *(process_one(rule_id, impact, node) for rule_id, impact, node in work_items)
        )
        results = [result for result, _ in outcomes]
        severities = [impact for _, impact in outcomes]

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

        for violation in payload.violations:
            for location in violation.locations:
                response = await self.gradient_client.analyze_pdf_page(
                    page_text_hint=location.evidence,
                    rule_context=f"Rule={violation.ruleId} Severity={violation.severity}",
                )
                summary, details, parse_error = self.gradient_client.normalize_pdf_output(response.text)
                results.append(
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
                    )
                )
                severities.append(violation.severity)

        summary = build_summary(results, severities)
        return FixReport(
            reportType="pdf",
            scanMetadata=payload.scanMetadata.model_dump(mode="json"),
            summary=summary,
            results=results,
        )
