from __future__ import annotations

import asyncio

from app.routing import load_routing_config


def test_routing_keywords_include_alt() -> None:
    cfg = load_routing_config("routing.yml")
    joined = " ".join(cfg.get("vision_keywords", []))
    assert "alt" in joined
