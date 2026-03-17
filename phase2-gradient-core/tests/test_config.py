from __future__ import annotations

import pytest

from app.config import load_settings


REQUIRED_KEYS = [
    "GRADIENT_API_KEY",
    "GRADIENT_CODER_MODEL_ID",
    "GRADIENT_VISION_MODEL_ID",
    "GRADIENT_PDF_MODEL_ID",
]


def test_missing_required_gradient_env_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in REQUIRED_KEYS:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError):
        load_settings()


def test_load_settings_with_required_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRADIENT_API_KEY", "test")
    monkeypatch.setenv("GRADIENT_CODER_MODEL_ID", "coder")
    monkeypatch.setenv("GRADIENT_VISION_MODEL_ID", "vision")
    monkeypatch.setenv("GRADIENT_PDF_MODEL_ID", "pdf")
    monkeypatch.setenv("REMEDIATION_CACHE_ENABLED", "true")
    monkeypatch.setenv("REMEDIATION_CACHE_PATH", "/tmp/test-cache.sqlite3")

    settings = load_settings()
    assert settings.gradient_api_key == "test"
    assert settings.gradient_coder_model_id == "coder"
    assert settings.remediation_cache_enabled is True
    assert settings.remediation_cache_path == "/tmp/test-cache.sqlite3"
