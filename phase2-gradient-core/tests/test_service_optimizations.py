from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.contracts import AxeNode, AxeViolation, AxeViolationPayload, FixResult, PdfLocation, PdfScanMetadata, PdfViolation, PdfViolationPayload, ScanMetadata
from app.gradient_client import GradientResponse
from app.service import SonarA11yService


class CountingGraph:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, state):
        self.calls += 1
        node = state["node"]
        return {
            "result": FixResult(
                ruleId=state["rule_id"],
                targetSelector=node.targetSelector,
                assignedAgent="coder_node",
                status="success",
                proposedHtml="<button>Fixed</button>",
                traceId="trace-1",
                modelId="coder-model",
                latencyMs=12,
            )
        }


class TimeoutGraph:
    async def ainvoke(self, _state):
        raise asyncio.TimeoutError()


class BatchGradientClient:
    def __init__(self) -> None:
        self.batch_calls = 0

    async def rewrite_html_batch(self, items, rule_id):
        self.batch_calls += 1
        results = [
            {
                "index": item["index"],
                "correctedHtml": f'<button data-rule="{rule_id}" data-target="{item["targetSelector"]}">Fixed</button>',
            }
            for item in items
        ]
        return GradientResponse(
            text=json.dumps({"results": results}),
            trace_id="batch-trace",
            model_id="coder-model",
            latency_ms=25,
            token_usage={"input": 100, "output": 50, "total": 150},
            cost_usd=None,
        )

    @staticmethod
    def normalize_coder_batch_output(response_text: str, expected_count: int):
        payload = json.loads(response_text)
        mapping = {
            int(item["index"]): item["correctedHtml"]
            for item in payload.get("results", [])
            if isinstance(item, dict) and "index" in item and "correctedHtml" in item
        }
        if len(mapping) != expected_count:
            return mapping, "Missing batched results."
        return mapping, None


class TimeoutBatchGradientClient(BatchGradientClient):
    async def rewrite_html_batch(self, items, rule_id):
        raise asyncio.TimeoutError()


class PdfGradientClient:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def analyze_pdf_page(self, page_text_hint, rule_context):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return GradientResponse(
            text=json.dumps({
                "summary": f"Review {rule_context}",
                "actions": [
                    {"title": "Set title", "fix": "Add PDF title metadata.", "suggestion": "Document Title"}
                ],
            }),
            trace_id="pdf-trace",
            model_id="pdf-model",
            latency_ms=15,
            token_usage={"input": 30, "output": 20, "total": 50},
            cost_usd=None,
        )

    @staticmethod
    def normalize_pdf_output(response_text: str):
        payload = json.loads(response_text)
        return payload.get("summary"), {"actions": payload.get("actions", [])}, None


def _settings(tmp_path: Path, timeout_seconds: int = 45) -> Settings:
    return Settings(
        port=8000,
        phase1_scanner_url="http://phase1-scanner:4001",
        gradient_api_key="test-key",
        gradient_base_url="https://inference.do-ai.run/v1",
        gradient_coder_model_id="coder-model",
        gradient_vision_model_id="vision-model",
        gradient_pdf_model_id="pdf-model",
        gradient_request_timeout_seconds=30,
        pdf_job_ttl_seconds=3600,
        web_parallelism=4,
        web_node_timeout_seconds=timeout_seconds,
        web_node_max_retries=1,
        web_retry_backoff_seconds=0,
        web_batch_size=4,
        max_web_nodes=30,
        remediation_cache_enabled=True,
        remediation_cache_path=str(tmp_path / "remediation-cache.sqlite3"),
    )


def _payload_with_duplicate_nodes() -> AxeViolationPayload:
    return AxeViolationPayload(
        scanMetadata=ScanMetadata(
            url="https://example.com",
            timestamp=datetime.now(timezone.utc),
            viewport="1920x1080",
        ),
        violations=[
            AxeViolation(
                ruleId="heading-order",
                impact="serious",
                description="Heading levels should only increase by one",
                nodes=[
                    AxeNode(
                        targetSelector=".slide-a",
                        rawHtml="<h4>Section title</h4>",
                        failureSummary="Heading order invalid. Fix heading levels so they only increase by one.",
                    ),
                    AxeNode(
                        targetSelector=".slide-b",
                        rawHtml="<h4>Section title</h4>",
                        failureSummary="Heading order invalid. Fix heading levels so they only increase by one.",
                    ),
                ],
            )
        ],
    )


def _payload_with_unique_coder_nodes() -> AxeViolationPayload:
    return AxeViolationPayload(
        scanMetadata=ScanMetadata(
            url="https://example.com",
            timestamp=datetime.now(timezone.utc),
            viewport="1920x1080",
        ),
        violations=[
            AxeViolation(
                ruleId="heading-order",
                impact="serious",
                description="Heading order issues",
                nodes=[
                    AxeNode(
                        targetSelector=".cta-primary",
                        rawHtml="<h4>Apply now</h4>",
                        failureSummary="Heading order invalid. Fix heading levels so they only increase by one.",
                    ),
                    AxeNode(
                        targetSelector=".cta-secondary",
                        rawHtml="<h4>Learn more</h4>",
                        failureSummary="Heading order invalid. Fix heading levels so they only increase by one.",
                    ),
                ],
            )
        ],
    )


@pytest.mark.asyncio
async def test_process_web_payload_deduplicates_equivalent_nodes(tmp_path: Path) -> None:
    service = SonarA11yService(_settings(tmp_path))
    batch_client = BatchGradientClient()
    service.gradient_client = batch_client

    report = await service.process_web_payload(_payload_with_duplicate_nodes())

    assert batch_client.batch_calls == 1
    assert report.summary.totalFindings == 2
    assert report.results[0].status == "success"
    assert report.results[1].status == "success"
    assert report.results[1].rationale == "Reused remediation from an equivalent duplicate finding."


@pytest.mark.asyncio
async def test_process_web_payload_populates_timeout_error_message(tmp_path: Path) -> None:
    service = SonarA11yService(_settings(tmp_path, timeout_seconds=7))
    service.gradient_client = TimeoutBatchGradientClient()
    service.router_graph = TimeoutGraph()

    report = await service.process_web_payload(_payload_with_duplicate_nodes())
    assert report.results[0].status == "error"
    assert report.results[0].error == "Node remediation timed out after 7s."


@pytest.mark.asyncio
async def test_process_web_payload_batches_unique_coder_nodes_by_rule(tmp_path: Path) -> None:
    service = SonarA11yService(_settings(tmp_path))
    batch_client = BatchGradientClient()
    service.gradient_client = batch_client

    report = await service.process_web_payload(_payload_with_unique_coder_nodes())

    assert batch_client.batch_calls == 1
    assert report.summary.totalFindings == 2
    assert report.results[0].proposedHtml is not None
    assert 'data-target=".cta-primary"' in report.results[0].proposedHtml
    assert 'data-target=".cta-secondary"' in report.results[1].proposedHtml


@pytest.mark.asyncio
async def test_process_web_payload_uses_heuristic_for_nested_interactive(tmp_path: Path) -> None:
    service = SonarA11yService(_settings(tmp_path))
    batch_client = BatchGradientClient()
    service.gradient_client = batch_client

    payload = AxeViolationPayload(
        scanMetadata=ScanMetadata(
            url="https://example.com",
            timestamp=datetime.now(timezone.utc),
            viewport="1920x1080",
        ),
        violations=[
            AxeViolation(
                ruleId="nested-interactive",
                impact="serious",
                description="Nested interactive controls",
                nodes=[
                    AxeNode(
                        targetSelector=".case-study-card",
                        rawHtml='<div role="button" tabindex="0" aria-label="" onclick="window.location=\'/x\'"><a href="/x">View Project</a></div>',
                        failureSummary="Interactive control contains a nested interactive element.",
                    )
                ],
            )
        ],
    )

    report = await service.process_web_payload(payload)

    assert batch_client.batch_calls == 0
    assert report.results[0].status == "success"
    assert 'role="button"' not in (report.results[0].proposedHtml or "")
    assert 'onclick=' not in (report.results[0].proposedHtml or "")
    assert report.results[0].modelId == "heuristic-fast-path"


@pytest.mark.asyncio
async def test_process_web_payload_uses_heuristic_for_color_contrast(tmp_path: Path) -> None:
    service = SonarA11yService(_settings(tmp_path))
    batch_client = BatchGradientClient()
    service.gradient_client = batch_client

    payload = AxeViolationPayload(
        scanMetadata=ScanMetadata(
            url="https://example.com",
            timestamp=datetime.now(timezone.utc),
            viewport="1920x1080",
        ),
        violations=[
            AxeViolation(
                ruleId="color-contrast",
                impact="serious",
                description="Contrast issue",
                nodes=[
                    AxeNode(
                        targetSelector=".cta-link",
                        rawHtml='<a class="cta-link" href="/apply">Apply now</a>',
                        failureSummary="Element has insufficient color contrast of 2.98 (foreground color: #ffffff, background color: #00a4bd, font size: 12px). Expected contrast ratio of 4.5:1",
                    )
                ],
            )
        ],
    )

    report = await service.process_web_payload(payload)

    assert batch_client.batch_calls == 0
    assert report.results[0].status == "success"
    assert "style=" in (report.results[0].proposedHtml or "")
    assert "color: #000000" in (report.results[0].proposedHtml or "")
    assert report.results[0].modelId == "heuristic-fast-path"
    assert report.results[0].details is not None
    assert report.results[0].details["actions"][0]["title"] == "This text does not have enough contrast against its background."
    assert report.results[0].details["actions"][0]["suggestion"] == "Set text color to #000000 and keep underline styling."


@pytest.mark.asyncio
async def test_process_web_payload_reuses_persistent_cache_across_service_instances(tmp_path: Path) -> None:
    payload = _payload_with_duplicate_nodes()

    first_service = SonarA11yService(_settings(tmp_path))
    first_batch_client = BatchGradientClient()
    first_service.gradient_client = first_batch_client

    first_report = await first_service.process_web_payload(payload)
    assert first_batch_client.batch_calls == 1
    assert first_report.results[0].status == "success"

    second_service = SonarA11yService(_settings(tmp_path))
    second_batch_client = BatchGradientClient()
    second_service.gradient_client = second_batch_client

    second_report = await second_service.process_web_payload(payload)

    assert second_batch_client.batch_calls == 0
    assert second_report.results[0].status == "success"
    assert second_report.results[0].rationale == "Reused remediation from persistent cache."
    assert second_service.cache_stats()["entries"] == 1
    assert second_service.cache_stats()["totalHits"] >= 1


@pytest.mark.asyncio
async def test_process_pdf_payload_runs_locations_concurrently(tmp_path: Path) -> None:
    service = SonarA11yService(_settings(tmp_path))
    pdf_client = PdfGradientClient()
    service.gradient_client = pdf_client

    payload = PdfViolationPayload(
        scanMetadata=PdfScanMetadata(
            source="https://example.com/test.pdf",
            filename="test.pdf",
            timestamp=datetime.now(timezone.utc),
            documentHash="abc123",
            pageCount=2,
        ),
        violations=[
            PdfViolation(
                ruleId="pdfua-title-missing",
                severity="moderate",
                description="PDF title missing",
                locations=[
                    PdfLocation(page=1, evidence="Page 1 text", ocrDerived=False),
                    PdfLocation(page=2, evidence="Page 2 text", ocrDerived=False),
                ],
            ),
            PdfViolation(
                ruleId="pdfua-document-lang-missing",
                severity="moderate",
                description="PDF language missing",
                locations=[
                    PdfLocation(page=1, evidence="Catalog Lang missing", ocrDerived=False),
                ],
            ),
        ],
    )

    report = await service.process_pdf_payload(payload)

    assert report.summary.totalFindings == 3
    assert pdf_client.max_active >= 2
