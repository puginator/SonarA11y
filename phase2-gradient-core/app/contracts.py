from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class ScanMetadata(BaseModel):
    url: HttpUrl
    timestamp: datetime
    viewport: str | None = None


class AxeNode(BaseModel):
    targetSelector: str
    rawHtml: str
    failureSummary: str
    elementScreenshotBase64: str | None = None
    warning: str | None = None


class AxeViolation(BaseModel):
    ruleId: str
    impact: Literal["minor", "moderate", "serious", "critical"]
    description: str
    nodes: list[AxeNode]


class AxeViolationPayload(BaseModel):
    scanMetadata: ScanMetadata
    violations: list[AxeViolation]


class PdfScanMetadata(BaseModel):
    source: str
    filename: str | None = None
    timestamp: datetime
    documentHash: str
    pageCount: int


class PdfLocation(BaseModel):
    page: int
    objectRef: str | None = None
    evidence: str
    ocrDerived: bool
    ocrConfidence: float | None = None


class PdfViolation(BaseModel):
    ruleId: str
    severity: Literal["minor", "moderate", "serious", "critical"]
    description: str
    pdfUaReference: str | None = None
    wcagReference: str | None = None
    locations: list[PdfLocation]


class PdfViolationPayload(BaseModel):
    scanMetadata: PdfScanMetadata
    violations: list[PdfViolation]


class FixResult(BaseModel):
    ruleId: str
    targetSelector: str | None = None
    page: int | None = None
    assignedAgent: Literal["vision_node", "coder_node", "pdf_node"]
    status: Literal["success", "skipped", "error"]
    proposedHtml: str | None = None
    proposedAltText: str | None = None
    confidence: float | None = None
    rationale: str | None = None
    traceId: str
    modelId: str
    latencyMs: int
    tokenUsage: dict[str, int] | None = None
    costUsd: float | None = None
    error: str | None = None
    details: dict[str, Any] | None = None


class FixSummary(BaseModel):
    totalFindings: int
    byAgent: dict[str, int]
    bySeverity: dict[str, int]


class FixReport(BaseModel):
    provider: Literal["digitalocean-gradient"] = "digitalocean-gradient"
    reportType: Literal["web", "pdf"]
    scanMetadata: dict[str, Any]
    summary: FixSummary
    results: list[FixResult]


class ProcessRequest(BaseModel):
    payload: AxeViolationPayload


class ScanAndProcessRequest(BaseModel):
    url: HttpUrl
    viewport: dict[str, int] | None = None
