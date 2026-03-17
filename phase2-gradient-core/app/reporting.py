from __future__ import annotations

from collections import Counter
from html import escape
from io import BytesIO
from typing import Iterable

from fpdf import FPDF

from .contracts import FixReport, FixResult, FixSummary


def build_summary(results: Iterable[FixResult], severities: Iterable[str]) -> FixSummary:
    result_list = list(results)
    by_agent = Counter(r.assignedAgent for r in result_list)
    by_severity = Counter(severities)
    return FixSummary(
        totalFindings=len(result_list),
        byAgent=dict(by_agent),
        bySeverity=dict(by_severity),
    )


def render_html_report(report: FixReport) -> str:
    rows = []
    for row in _expand_report_rows(report.results):
        rows.append(
            "<tr>"
            f"<td>{escape(row['ruleId'])}</td>"
            f"<td>{escape(row['location'])}</td>"
            f"<td><pre>{escape(row['issue'])}</pre></td>"
            f"<td><pre>{escape(row['remediation'])}</pre></td>"
            f"<td><pre>{escape(row['suggestedValue'])}</pre></td>"
            f"<td>{escape(row['status'])}</td>"
            f"<td>{escape(row['traceId'])}</td>"
            f"<td><pre>{escape(row['notes'])}</pre></td>"
            "</tr>"
        )

    if not rows:
        rows.append(
            "<tr><td colspan='8'>No findings were generated for this report. "
            "The PDF may have passed current heuristics, or the scan did not extract any actionable evidence.</td></tr>"
        )

    return f"""
<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <title>SonarA11y Fix Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 24px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .meta {{ margin-bottom: 16px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f2f2f2; }}
    pre {{ white-space: pre-wrap; margin: 0; }}
    .obs {{ background: #eef6ff; padding: 12px; margin-top: 16px; border: 1px solid #c5e0ff; }}
  </style>
</head>
<body>
  <h1>SonarA11y Report ({report.reportType})</h1>
  <div class='meta'>
    <strong>Provider:</strong> {escape(report.provider)}<br/>
    <strong>Total Findings:</strong> {report.summary.totalFindings}
  </div>
  <table>
    <thead>
      <tr>
        <th>Rule ID</th>
        <th>Location</th>
        <th>Issue</th>
        <th>Remediation</th>
        <th>Suggested Value</th>
        <th>Status</th>
        <th>Trace ID</th>
        <th>Notes</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  <div class='obs'>
    <h2>Gradient Observability</h2>
    <p>Use the trace IDs above in DigitalOcean Gradient Control Panel to inspect routing and token usage.</p>
  </div>
</body>
</html>
""".strip()


def render_pdf_report(report: FixReport) -> bytes:
    pdf = FPDF()
    pdf.set_compression(False)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    _pdf_write_line(pdf, f"SonarA11y Report ({report.reportType})", style=("Helvetica", "B", 16), line_height=10)

    _pdf_write_line(pdf, f"Provider: {report.provider}", style=("Helvetica", "", 11), line_height=8)
    _pdf_write_line(pdf, f"Total Findings: {report.summary.totalFindings}", style=("Helvetica", "", 11), line_height=8)

    pdf.ln(4)
    _pdf_write_line(pdf, "Gradient Observability", style=("Helvetica", "B", 12), line_height=8)
    _pdf_write_line(
        pdf,
        "Trace IDs are included per finding for Gradient Control Panel inspection.",
        style=("Helvetica", "", 10),
        line_height=6,
    )

    pdf.ln(4)
    if not report.results:
        _pdf_write_line(pdf, "No findings generated", style=("Helvetica", "B", 11), line_height=8)
        _pdf_write_line(
            pdf,
            "The PDF scan did not produce actionable findings under current heuristics. "
            "This can indicate a relatively clean document or a document that requires deeper manual review.",
            style=("Helvetica", "", 10),
            line_height=6,
        )

    for item in report.results:
        for row in _expand_item_rows(item):
            _pdf_write_line(pdf, f"{row['ruleId']} | {row['status']}", style=("Helvetica", "B", 10), line_height=6)
            _pdf_write_line(pdf, f"Location: {row['location']}", style=("Helvetica", "", 9), line_height=6)
            _pdf_write_line(pdf, f"Issue: {row['issue'][:500]}", style=("Helvetica", "", 9), line_height=6)
            _pdf_write_line(pdf, f"Remediation: {row['remediation'][:500]}", style=("Helvetica", "", 9), line_height=6)
            if row["suggestedValue"] != "-":
                _pdf_write_line(
                    pdf,
                    f"Suggested value: {row['suggestedValue'][:500]}",
                    style=("Helvetica", "", 9),
                    line_height=6,
                )
            _pdf_write_line(pdf, f"Trace: {row['traceId']}", style=("Helvetica", "", 9), line_height=6)
            if row["notes"] != "-":
                _pdf_write_line(pdf, f"Notes: {row['notes'][:500]}", style=("Helvetica", "", 9), line_height=6)
        pdf.ln(2)

    raw = pdf.output(dest="S")
    if isinstance(raw, str):
        return raw.encode("latin-1")
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    out = BytesIO()
    out.write(bytes(raw))
    return out.getvalue()


def _expand_report_rows(results: Iterable[FixResult]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in results:
        rows.extend(_expand_item_rows(item))
    return rows


def _expand_item_rows(item: FixResult) -> list[dict[str, str]]:
    location = item.targetSelector or (f"Page {item.page}" if item.page else "-")
    summary = item.proposedHtml or item.proposedAltText or item.error or "N/A"
    notes_parts = [item.rationale or ""]

    if item.details:
        expected = item.details.get("expectedOutcome")
        if isinstance(expected, str) and expected.strip():
            notes_parts.append(f"Expected outcome: {expected.strip()}")
        limitations = item.details.get("limitations")
        if isinstance(limitations, list) and limitations:
            notes_parts.append(f"Limitation: {str(limitations[0]).strip()}")

    notes = "\n".join(part for part in notes_parts if part).strip() or "-"
    actions = item.details.get("actions") if item.details else None
    if isinstance(actions, list) and actions:
        rows = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            rows.append({
                "ruleId": item.ruleId,
                "location": location,
                "issue": str(action.get("title") or "Recommended fix").strip(),
                "remediation": str(action.get("fix") or summary).strip() or summary,
                "suggestedValue": str(action.get("suggestion") or "-").strip() or "-",
                "status": item.status,
                "traceId": item.traceId,
                "notes": notes,
            })
        if rows:
            return rows

    return [{
        "ruleId": item.ruleId,
        "location": location,
        "issue": item.rationale or item.ruleId,
        "remediation": summary,
        "suggestedValue": "-",
        "status": item.status,
        "traceId": item.traceId,
        "notes": notes,
    }]


def _pdf_write_line(
    pdf: FPDF,
    text: str,
    *,
    style: tuple[str, str, int],
    line_height: int,
) -> None:
    family, emphasis, size = style
    pdf.set_x(pdf.l_margin)
    pdf.set_font(family, emphasis, size)
    pdf.multi_cell(0, line_height, _pdf_safe_text(text))


def _pdf_safe_text(text: str) -> str:
    # Give FPDF break opportunities for long tokens such as trace IDs and URLs.
    safe = str(text)
    safe = safe.replace("/", "/ ")
    safe = safe.replace("-", "- ")
    safe = safe.replace("_", "_ ")
    return safe
