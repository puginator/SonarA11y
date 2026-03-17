from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from pypdf import PdfReader

from .contracts import PdfLocation, PdfViolation, PdfViolationPayload, PdfScanMetadata
from .gradient_client import GradientInferenceClient


async def scan_pdf_bytes(
    *,
    source: str,
    filename: str | None,
    data: bytes,
    gradient_client: GradientInferenceClient,
) -> PdfViolationPayload:
    document_hash = hashlib.sha256(data).hexdigest()
    reader = PdfReader(BytesIO(data))

    violations: list[PdfViolation] = []
    text_samples: list[str] = []
    catalog = reader.trailer.get("/Root", {})
    mark_info = catalog.get("/MarkInfo") if hasattr(catalog, "get") else None
    is_marked = bool(mark_info.get("/Marked")) if hasattr(mark_info, "get") else False
    has_struct_tree = bool(catalog.get("/StructTreeRoot")) if hasattr(catalog, "get") else False
    document_lang = catalog.get("/Lang") if hasattr(catalog, "get") else None
    metadata = reader.metadata or {}
    document_title = _metadata_value(metadata, "/Title")

    if not (is_marked and has_struct_tree):
        violations.append(
            PdfViolation(
                ruleId="pdfua-tagged-content-missing",
                severity="serious",
                description="Document does not expose both MarkInfo/Marked and StructTreeRoot; likely not properly tagged for assistive technology.",
                pdfUaReference="PDF/UA-1 7.1",
                wcagReference="WCAG 1.3.1",
                locations=[
                    PdfLocation(
                        page=1,
                        evidence="Catalog is missing tagged-PDF markers (MarkInfo/Marked and/or StructTreeRoot).",
                        ocrDerived=False,
                    )
                ],
            )
        )

    if not document_lang:
        violations.append(
            PdfViolation(
                ruleId="pdfua-document-lang-missing",
                severity="moderate",
                description="Document language is not declared in the PDF catalog.",
                pdfUaReference="PDF/UA-1 7.9",
                wcagReference="WCAG 3.1.1",
                locations=[
                    PdfLocation(
                        page=1,
                        evidence="Catalog /Lang entry is missing.",
                        ocrDerived=False,
                    )
                ],
            )
        )

    if not document_title:
        violations.append(
            PdfViolation(
                ruleId="pdfua-title-missing",
                severity="moderate",
                description="Document metadata does not include a title.",
                pdfUaReference="PDF/UA-1 7.18",
                wcagReference="WCAG 2.4.2",
                locations=[
                    PdfLocation(
                        page=1,
                        evidence="Metadata /Title entry is missing or empty.",
                        ocrDerived=False,
                    )
                ],
            )
        )

    for idx, page in enumerate(reader.pages, start=1):
        extracted = (page.extract_text() or "").strip()
        if extracted:
            if len(text_samples) < 3:
                text_samples.append(f"Page {idx}: {extracted[:400]}")
            if len(extracted) < 20:
                violations.append(
                    PdfViolation(
                        ruleId="pdfua-reading-order",
                        severity="moderate",
                        description="Page text is sparse; review logical reading order and structure tags.",
                        pdfUaReference="PDF/UA-1 7.18",
                        wcagReference="WCAG 1.3.2",
                        locations=[
                            PdfLocation(page=idx, evidence=extracted[:200], ocrDerived=False)
                        ],
                    )
                )
            elif not (is_marked and has_struct_tree):
                violations.append(
                    PdfViolation(
                        ruleId="pdfua-page-structure-review",
                        severity="moderate",
                        description="Page contains extractable text, but document tagging is missing; verify headings, lists, tables, and reading order manually.",
                        pdfUaReference="PDF/UA-1 7.1",
                        wcagReference="WCAG 1.3.1",
                        locations=[
                            PdfLocation(page=idx, evidence=extracted[:240], ocrDerived=False)
                        ],
                    )
                )
            continue

        ocr_response = await gradient_client.analyze_pdf_page(
            page_text_hint="",
            rule_context="Image-only page detected. Provide OCR-style accessibility guidance.",
        )
        violations.append(
            PdfViolation(
                ruleId="pdfua-tagged-content-missing",
                severity="serious",
                description="No extractable text detected; document likely image-based without tags.",
                pdfUaReference="PDF/UA-1 7.1",
                wcagReference="WCAG 1.3.1",
                locations=[
                    PdfLocation(
                        page=idx,
                        evidence=ocr_response.text[:300] or "OCR fallback used.",
                        ocrDerived=True,
                        ocrConfidence=0.55,
                    )
                ],
            )
        )

    if not violations:
        review_evidence = "\n\n".join(text_samples[:3]) or "No extractable text samples were retained for review."
        violations.append(
            PdfViolation(
                ruleId="pdfua-manual-review",
                severity="moderate",
                description="Automated heuristics did not detect explicit PDF accessibility violations. Run a Gradient-guided manual review for document title, language, heading structure, table markup, form fields, and reading order.",
                pdfUaReference="PDF/UA-1 manual review",
                wcagReference="WCAG 1.3.1 / 2.4.2 / 3.1.1",
                locations=[
                    PdfLocation(
                        page=1,
                        evidence=review_evidence[:1200],
                        ocrDerived=False,
                    )
                ],
            )
        )

    scan_metadata = PdfScanMetadata(
        source=source,
        filename=filename,
        timestamp=datetime.now(timezone.utc),
        documentHash=document_hash,
        pageCount=max(1, len(reader.pages)),
    )

    return PdfViolationPayload(scanMetadata=scan_metadata, violations=violations)


def _metadata_value(metadata: Any, key: str) -> str | None:
    if metadata is None:
        return None
    value = None
    if hasattr(metadata, "get"):
        value = metadata.get(key)
    if value is None:
        attr_name = key.lstrip("/").lower()
        value = getattr(metadata, attr_name, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None
