from __future__ import annotations

from app.gradient_client import GradientInferenceClient


def test_normalize_coder_output_extracts_corrected_html_from_json() -> None:
    payload = """
    {
      "provider": "digitalocean-gradient",
      "results": [
        {
          "status": "fixed",
          "correctedHtml": "<a href=\\"/privacy\\" style=\\"color:#005c6d\\">Privacy Policy</a>"
        }
      ]
    }
    """
    corrected, error = GradientInferenceClient.normalize_coder_output(payload)
    assert error is None
    assert corrected == '<a href="/privacy" style="color:#005c6d">Privacy Policy</a>'


def test_normalize_coder_output_returns_error_on_failed_payload() -> None:
    payload = """
    {
      "status": "failed",
      "error": "Missing required DigitalOcean Gradient model ID."
    }
    """
    corrected, error = GradientInferenceClient.normalize_coder_output(payload)
    assert corrected is None
    assert error is not None
    assert "Missing required DigitalOcean Gradient model ID." in error


def test_normalize_alt_output_extracts_alt_value() -> None:
    text, error = GradientInferenceClient.normalize_alt_output(
        '<img src="/hero.png" alt="City hall exterior with ramp entrance">'
    )
    assert error is None
    assert text == "City hall exterior with ramp entrance"


def test_normalize_pdf_output_extracts_summary_and_actions() -> None:
    payload = """
    {
      "summary": "PDF needs title, language, and structure fixes.",
      "actions": [
        {"title": "Set title", "fix": "Add a descriptive document title.", "suggestion": "Invoice - Reservation #1219845"},
        {"title": "Set language", "fix": "Declare en-US in PDF metadata.", "suggestion": "en-US"}
      ],
      "expectedOutcome": "Screen readers can identify the document and navigate sections."
    }
    """
    summary, details, error = GradientInferenceClient.normalize_pdf_output(payload)
    assert error is None
    assert summary == "PDF needs title, language, and structure fixes."
    assert details is not None
    assert len(details["actions"]) == 2
    assert details["actions"][0]["suggestion"] == "Invoice - Reservation #1219845"
    assert details["expectedOutcome"] == "Screen readers can identify the document and navigate sections."


def test_pdf_document_guidance_detects_reservation_invoice() -> None:
    guidance = GradientInferenceClient._pdf_document_guidance(
        "Reservation ID: 1219845 Check In: Mar 12 Payment total: $240",
        "Rule=pdfua-manual-review Severity=moderate",
    )
    assert "reservation/invoice" in guidance.lower()
    assert "Reservation Invoice - 1219845" in guidance
