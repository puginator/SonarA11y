from __future__ import annotations

from typing import Any, TypedDict

import yaml
from langgraph.graph import END, START, StateGraph

from .contracts import AxeNode, FixResult
from .gradient_client import GradientInferenceClient


class FindingState(TypedDict, total=False):
    rule_id: str
    node: AxeNode
    route: str
    result: FixResult


def load_routing_config(path: str = "routing.yml") -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def build_router_graph(
    gradient: GradientInferenceClient,
    trace,
    routing_config: dict[str, Any],
):
    vision_keywords = [s.lower() for s in routing_config.get("vision_keywords", [])]

    @trace(name="router_node")
    async def router_node(state: FindingState) -> dict[str, str]:
        rule_id = state["rule_id"].lower()
        route = "vision_node" if any(k in rule_id for k in vision_keywords) else "coder_node"
        return {"route": route}

    @trace(name="vision_node")
    async def vision_node(state: FindingState) -> dict[str, FixResult]:
        node = state["node"]
        screenshot = node.elementScreenshotBase64 or ""
        if not screenshot:
            return {
                "result": FixResult(
                    ruleId=state["rule_id"],
                    targetSelector=node.targetSelector,
                    assignedAgent="vision_node",
                    status="skipped",
                    rationale="Vision route selected but screenshot was not available.",
                    traceId="no-screenshot",
                    modelId="N/A",
                    latencyMs=0,
                    error="Missing element screenshot",
                )
            }

        response = await gradient.generate_alt_text(screenshot, node.rawHtml, node.failureSummary)
        normalizer = getattr(gradient, "normalize_alt_output", None)
        if callable(normalizer):
            alt_text, parse_error = normalizer(response.text)
        else:
            alt_text, parse_error = response.text, None

        status = "success" if alt_text else "error"
        return {
            "result": FixResult(
                ruleId=state["rule_id"],
                targetSelector=node.targetSelector,
                assignedAgent="vision_node",
                status=status,
                proposedAltText=alt_text,
                confidence=0.8 if alt_text else None,
                rationale=(
                    "Generated from visual and HTML context."
                    if alt_text
                    else "Vision model output could not be normalized into alt text."
                ),
                traceId=response.trace_id,
                modelId=response.model_id,
                latencyMs=response.latency_ms,
                tokenUsage=response.token_usage,
                costUsd=response.cost_usd,
                error=parse_error,
            )
        }

    @trace(name="coder_node")
    async def coder_node(state: FindingState) -> dict[str, FixResult]:
        node = state["node"]
        response = await gradient.rewrite_html(node.rawHtml, node.failureSummary, state["rule_id"])
        normalizer = getattr(gradient, "normalize_coder_output", None)
        if callable(normalizer):
            corrected_html, parse_error = normalizer(response.text)
        else:
            corrected_html, parse_error = response.text, None

        status = "success" if corrected_html else "error"
        return {
            "result": FixResult(
                ruleId=state["rule_id"],
                targetSelector=node.targetSelector,
                assignedAgent="coder_node",
                status=status,
                proposedHtml=corrected_html,
                confidence=0.85 if corrected_html else None,
                rationale=(
                    "Generated from failure summary and source HTML."
                    if corrected_html
                    else "Coder model output could not be normalized into corrected HTML."
                ),
                traceId=response.trace_id,
                modelId=response.model_id,
                latencyMs=response.latency_ms,
                tokenUsage=response.token_usage,
                costUsd=response.cost_usd,
                error=parse_error,
            )
        }

    def branch(state: FindingState) -> str:
        return state["route"]

    graph = StateGraph(FindingState)
    graph.add_node("router_node", router_node)
    graph.add_node("vision_node", vision_node)
    graph.add_node("coder_node", coder_node)

    graph.add_edge(START, "router_node")
    graph.add_conditional_edges("router_node", branch, {
        "vision_node": "vision_node",
        "coder_node": "coder_node",
    })
    graph.add_edge("vision_node", END)
    graph.add_edge("coder_node", END)

    return graph.compile()
