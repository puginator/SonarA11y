from __future__ import annotations

from pathlib import Path

FORBIDDEN = ["openai", "anthropic", "cohere", "gemini", "mistral"]


def test_no_non_gradient_provider_references() -> None:
    root = Path(__file__).resolve().parents[1]
    app_dir = root / "app"

    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        content = path.read_text(encoding="utf-8").lower()
        for forbidden in FORBIDDEN:
            if forbidden in content:
                offenders.append(f"{path}: contains forbidden provider reference '{forbidden}'")

    assert not offenders, "\n".join(offenders)
