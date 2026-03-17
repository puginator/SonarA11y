from __future__ import annotations

import pytest

from app.contracts import AxeNode
from app.routing import build_router_graph


class DummyResponse:
    def __init__(self, text: str, model: str) -> None:
        self.text = text
        self.trace_id = "trace-123"
        self.model_id = model
        self.latency_ms = 42
        self.token_usage = {"input": 1, "output": 1, "total": 2}
        self.cost_usd = 0.001


class DummyGradient:
    async def generate_alt_text(self, *_args, **_kwargs):
        return DummyResponse("descriptive alt", "vision-model")

    async def rewrite_html(self, *_args, **_kwargs):
        return DummyResponse("<img alt='ok'>", "coder-model")


def passthrough_trace(**_kwargs):
    def dec(fn):
        return fn

    return dec


@pytest.mark.asyncio
async def test_routes_image_rule_to_vision() -> None:
    graph = build_router_graph(
        gradient=DummyGradient(),
        trace=passthrough_trace,
        routing_config={"vision_keywords": ["image", "alt"]},
    )

    state = {
        "rule_id": "image-alt",
        "node": AxeNode(
            targetSelector="img.logo",
            rawHtml="<img src='logo.png'>",
            failureSummary="Missing alt",
            elementScreenshotBase64="ZmFrZQ==",
        ),
    }

    result = await graph.ainvoke(state)
    assert result["result"].assignedAgent == "vision_node"
    assert result["result"].modelId == "vision-model"


@pytest.mark.asyncio
async def test_routes_other_rule_to_coder() -> None:
    graph = build_router_graph(
        gradient=DummyGradient(),
        trace=passthrough_trace,
        routing_config={"vision_keywords": ["image", "alt"]},
    )

    state = {
        "rule_id": "heading-order",
        "node": AxeNode(
            targetSelector="h3",
            rawHtml="<h3>Bad</h3>",
            failureSummary="Heading order",
        ),
    }

    result = await graph.ainvoke(state)
    assert result["result"].assignedAgent == "coder_node"
    assert result["result"].modelId == "coder-model"
