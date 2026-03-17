from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    port: int
    phase1_scanner_url: str
    gradient_api_key: str
    gradient_base_url: str
    gradient_coder_model_id: str
    gradient_vision_model_id: str
    gradient_pdf_model_id: str
    gradient_request_timeout_seconds: int
    pdf_job_ttl_seconds: int
    web_parallelism: int
    web_node_timeout_seconds: int
    max_web_nodes: int
    remediation_cache_enabled: bool
    remediation_cache_path: str


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_settings() -> Settings:
    cache_enabled = os.getenv("REMEDIATION_CACHE_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
    return Settings(
        port=int(os.getenv("PORT", "8000")),
        phase1_scanner_url=os.getenv("PHASE1_SCANNER_URL", "http://localhost:4001"),
        gradient_api_key=_require_env("GRADIENT_API_KEY"),
        gradient_base_url=os.getenv("GRADIENT_BASE_URL", "https://inference.do-ai.run/v1").rstrip("/"),
        gradient_coder_model_id=_require_env("GRADIENT_CODER_MODEL_ID"),
        gradient_vision_model_id=_require_env("GRADIENT_VISION_MODEL_ID"),
        gradient_pdf_model_id=_require_env("GRADIENT_PDF_MODEL_ID"),
        gradient_request_timeout_seconds=int(os.getenv("GRADIENT_REQUEST_TIMEOUT_SECONDS", "90")),
        pdf_job_ttl_seconds=int(os.getenv("PDF_JOB_TTL_SECONDS", "7200")),
        web_parallelism=int(os.getenv("WEB_PARALLELISM", "4")),
        web_node_timeout_seconds=int(os.getenv("WEB_NODE_TIMEOUT_SECONDS", "45")),
        max_web_nodes=int(os.getenv("MAX_WEB_NODES", "30")),
        remediation_cache_enabled=cache_enabled,
        remediation_cache_path=os.getenv("REMEDIATION_CACHE_PATH", "/tmp/sonara11y-remediation-cache.sqlite3"),
    )
