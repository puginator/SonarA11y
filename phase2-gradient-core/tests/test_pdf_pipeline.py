from __future__ import annotations

from fpdf import FPDF
import pytest

from app.pdf_pipeline import scan_pdf_bytes


class NoOcrGradient:
    async def analyze_pdf_page(self, *_args, **_kwargs):
        raise AssertionError("OCR fallback should not run for text-based PDF test fixture.")


def _build_text_pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 10, "This is a text-based PDF fixture for SonarA11y pipeline tests.")
    raw = pdf.output(dest="S")
    if isinstance(raw, str):
        return raw.encode("latin-1")
    return bytes(raw)


@pytest.mark.asyncio
async def test_scan_pdf_bytes_emits_document_level_findings_for_text_pdf() -> None:
    payload = await scan_pdf_bytes(
        source="upload",
        filename="fixture.pdf",
        data=_build_text_pdf(),
        gradient_client=NoOcrGradient(),
    )

    rule_ids = {violation.ruleId for violation in payload.violations}
    assert "pdfua-tagged-content-missing" in rule_ids
    assert "pdfua-document-lang-missing" in rule_ids
    assert "pdfua-title-missing" in rule_ids
    assert payload.scanMetadata.pageCount == 1


@pytest.mark.asyncio
async def test_scan_pdf_bytes_emits_manual_review_when_no_heuristic_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import pdf_pipeline

    class FakePage:
        def extract_text(self) -> str:
            return "Long enough extracted text for a manual-review fallback path."

    class FakeReader:
        def __init__(self, *_args, **_kwargs) -> None:
            self.pages = [FakePage()]
            self.trailer = {
                "/Root": {
                    "/MarkInfo": {"/Marked": True},
                    "/StructTreeRoot": object(),
                    "/Lang": "en-US",
                }
            }
            self.metadata = {"/Title": "Accessible Fixture"}

    monkeypatch.setattr(pdf_pipeline, "PdfReader", FakeReader)

    payload = await pdf_pipeline.scan_pdf_bytes(
        source="upload",
        filename="fixture-clean.pdf",
        data=b"%PDF-1.4 fake",
        gradient_client=NoOcrGradient(),
    )

    rule_ids = {violation.ruleId for violation in payload.violations}
    assert "pdfua-manual-review" in rule_ids
