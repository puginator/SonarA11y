from __future__ import annotations

from app.contracts import FixReport, FixResult, FixSummary
from app.reporting import render_html_report, render_pdf_report


def _empty_report() -> FixReport:
    return FixReport(
        reportType="pdf",
        scanMetadata={"source": "upload", "pageCount": 1},
        summary=FixSummary(totalFindings=0, byAgent={}, bySeverity={}),
        results=[],
    )


def test_render_html_report_shows_no_findings_message() -> None:
    html = render_html_report(_empty_report())
    assert "No findings were generated for this report." in html


def test_render_pdf_report_shows_no_findings_message() -> None:
    pdf_bytes = render_pdf_report(_empty_report())
    assert b"No findings generated" in pdf_bytes


def test_render_html_report_includes_actionable_fix_lines() -> None:
    report = FixReport(
        reportType="pdf",
        scanMetadata={"source": "upload", "pageCount": 1},
        summary=FixSummary(totalFindings=1, byAgent={"pdf_node": 1}, bySeverity={"moderate": 1}),
        results=[
            FixResult(
                ruleId="pdfua-manual-review",
                page=1,
                assignedAgent="pdf_node",
                status="success",
                proposedAltText="PDF needs title and language fixes.",
                rationale="Manual review fallback",
                traceId="trace-1",
                modelId="pdf-model",
                latencyMs=10,
                details={
                    "actions": [
                        {"title": "Set title", "fix": "Add a descriptive PDF title.", "suggestion": "Invoice - Reservation #1219845"},
                        {"title": "Set language", "fix": "Declare en-US in metadata."},
                    ]
                },
            )
        ],
    )

    html = render_html_report(report)
    assert "<th>Issue</th>" in html
    assert "<th>Suggested Value</th>" in html
    assert "Set title" in html
    assert "Add a descriptive PDF title." in html
    assert "Invoice - Reservation #1219845" in html


def test_render_pdf_report_handles_action_rows_without_fpdf_layout_error() -> None:
    report = FixReport(
        reportType="pdf",
        scanMetadata={"source": "upload", "pageCount": 1},
        summary=FixSummary(totalFindings=1, byAgent={"pdf_node": 1}, bySeverity={"moderate": 1}),
        results=[
            FixResult(
                ruleId="pdfua-manual-review",
                page=1,
                assignedAgent="pdf_node",
                status="success",
                proposedAltText="PDF needs title and language fixes.",
                rationale="Manual review fallback",
                traceId="92e9bcc8-8238-4210-8dd7-0d9be19f771b",
                modelId="pdf-model",
                latencyMs=10,
                details={
                    "actions": [
                        {
                            "title": "Set title",
                            "fix": "Add a descriptive PDF title.",
                            "suggestion": "Reservation Invoice - Paid - Reservation #1219845",
                        },
                        {
                            "title": "Set language",
                            "fix": "Declare en-US in metadata.",
                            "suggestion": "en-US",
                        },
                    ]
                },
            )
        ],
    )

    pdf_bytes = render_pdf_report(report)
    assert pdf_bytes.startswith(b"%PDF")
    assert b"Reservation Invoice" in pdf_bytes
