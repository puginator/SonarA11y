from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.contracts import AxeNode, AxeViolation, AxeViolationPayload, FixResult, ScanMetadata
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
                ruleId="nested-interactive",
                impact="serious",
                description="Nested interactive controls",
                nodes=[
                    AxeNode(
                        targetSelector=".slide-a",
                        rawHtml='<div role="button"><a href="/x">View</a></div>',
                        failureSummary="Interactive control contains a nested interactive element.",
                    ),
                    AxeNode(
                        targetSelector=".slide-b",
                        rawHtml='<div role="button"><a href="/x">View</a></div>',
                        failureSummary="Interactive control contains a nested interactive element.",
                    ),
                ],
            )
        ],
    )


@pytest.mark.asyncio
async def test_process_web_payload_deduplicates_equivalent_nodes(tmp_path: Path) -> None:
    service = SonarA11yService(_settings(tmp_path))
    graph = CountingGraph()
    service.router_graph = graph

    report = await service.process_web_payload(_payload_with_duplicate_nodes())

    assert graph.calls == 1
    assert report.summary.totalFindings == 2
    assert report.results[0].status == "success"
    assert report.results[1].status == "success"
    assert report.results[1].rationale == "Reused remediation from an equivalent duplicate finding."


@pytest.mark.asyncio
async def test_process_web_payload_populates_timeout_error_message(tmp_path: Path) -> None:
    service = SonarA11yService(_settings(tmp_path, timeout_seconds=7))
    service.router_graph = TimeoutGraph()

    report = await service.process_web_payload(_payload_with_duplicate_nodes())
    assert report.results[0].status == "error"
    assert report.results[0].error == "Node remediation timed out after 7s."


@pytest.mark.asyncio
async def test_process_web_payload_reuses_persistent_cache_across_service_instances(tmp_path: Path) -> None:
    payload = _payload_with_duplicate_nodes()

    first_service = SonarA11yService(_settings(tmp_path))
    first_graph = CountingGraph()
    first_service.router_graph = first_graph

    first_report = await first_service.process_web_payload(payload)
    assert first_graph.calls == 1
    assert first_report.results[0].status == "success"

    second_service = SonarA11yService(_settings(tmp_path))
    second_graph = CountingGraph()
    second_service.router_graph = second_graph

    second_report = await second_service.process_web_payload(payload)

    assert second_graph.calls == 0
    assert second_report.results[0].status == "success"
    assert second_report.results[0].rationale == "Reused remediation from persistent cache."
    assert second_service.cache_stats()["entries"] == 1
    assert second_service.cache_stats()["totalHits"] >= 1
