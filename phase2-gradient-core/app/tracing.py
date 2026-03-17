from __future__ import annotations

from typing import Any, Callable


def resolve_trace_decorator() -> Callable[..., Callable[[Callable[..., Any]], Callable[..., Any]]]:
    """Resolve Gradient ADK tracing decorator at runtime.

    We keep resolution centralized so startup can fail explicitly if ADK is missing.
    """
    candidates = [
        ("gradient_adk", "trace_tool"),
        ("gradient_adk.tracing", "trace_tool"),
    ]

    for module_name, attr_name in candidates:
        try:
            module = __import__(module_name, fromlist=[attr_name])
            trace = getattr(module, attr_name, None)
            if callable(trace):
                return trace
        except Exception:
            continue

    raise RuntimeError(
        "gradient-adk trace_tool decorator not found. Install gradient-adk and verify tracing API availability."
    )
