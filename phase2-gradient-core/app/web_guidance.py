from __future__ import annotations

import re

from .contracts import AxeNode, FixResult


_ISSUE_LABELS = {
    "aria-prohibited-attr": "This element uses an ARIA attribute that is not allowed here.",
    "color-contrast": "This text does not have enough contrast against its background.",
    "heading-order": "The heading level is out of sequence for the page structure.",
    "image-alt": "This image needs useful alternative text.",
    "nested-interactive": "A clickable container also contains another clickable control.",
}

_REMEDIATION_LABELS = {
    "aria-prohibited-attr": "Remove the prohibited ARIA attribute or move the label to an element that supports it.",
    "color-contrast": "Adjust the text or background colors until the element reaches WCAG contrast requirements.",
    "heading-order": "Change the heading tag so headings move in a logical order without skipping levels.",
    "image-alt": "Add concise alt text that describes the image's purpose for a non-visual user.",
    "nested-interactive": "Keep a single interactive control. Remove button or link behavior from the outer wrapper or restructure the markup.",
}


def enrich_web_result(rule_id: str, node: AxeNode, result: FixResult) -> FixResult:
    details = dict(result.details or {})
    # Always refresh web action guidance so cached or older results do not keep
    # stale truncated suggestions from previous UI/report formats.
    details["actions"] = [
        {
            "title": explain_issue(rule_id, node, result),
            "fix": explain_remediation(rule_id, node, result),
            "suggestion": suggest_value(rule_id, node, result),
        }
    ]
    return result.model_copy(update={"details": details})


def explain_issue(rule_id: str, node: AxeNode, result: FixResult) -> str:
    label = _ISSUE_LABELS.get(rule_id.lower())
    if label:
        return label
    summary = _clean_sentence(node.failureSummary or result.error or result.rationale or rule_id)
    if summary:
        return summary
    return "This accessibility issue needs review."


def explain_remediation(rule_id: str, node: AxeNode, result: FixResult) -> str:
    if result.status == "error" and result.error:
        return _clean_sentence(result.error)

    label = _REMEDIATION_LABELS.get(rule_id.lower())
    if label:
        return label

    if result.proposedAltText:
        return "Use the suggested alt text on the image element."

    if result.proposedHtml:
        return "Apply the suggested HTML update to this element."

    return _clean_sentence(node.failureSummary or result.rationale or "Review this finding and apply the recommended correction.")


def suggest_value(rule_id: str, node: AxeNode, result: FixResult) -> str:
    lowered_rule = rule_id.lower()
    if result.status == "error":
        return "-"

    if result.proposedAltText:
        return f'alt="{result.proposedAltText}"'

    if lowered_rule == "color-contrast":
        color = _extract_style_property(result.proposedHtml or "", "color")
        if color:
            underline = _extract_style_property(result.proposedHtml or "", "text-decoration")
            if underline:
                return f"Set text color to {color} and keep {underline} styling."
            return f"Set text color to {color}."

    if lowered_rule == "heading-order":
        heading_match = re.search(r"<h([1-6])\b", result.proposedHtml or "", flags=re.IGNORECASE)
        if heading_match:
            return f"Use <h{heading_match.group(1)}> for this heading."

    if lowered_rule == "aria-prohibited-attr":
        if "aria-label" in (node.rawHtml or "").lower() and "aria-label" not in (result.proposedHtml or "").lower():
            return "Remove the aria-label attribute from the element."

    if lowered_rule == "nested-interactive":
        if (result.proposedHtml or ""):
            return _compact_html(result.proposedHtml)

    if result.proposedHtml:
        return _compact_html(result.proposedHtml)

    return "-"


def _extract_style_property(html: str, prop_name: str) -> str | None:
    style_match = re.search(r"""style\s*=\s*(["'])(.*?)\1""", html or "", flags=re.IGNORECASE | re.DOTALL)
    if not style_match:
        return None
    for chunk in style_match.group(2).split(";"):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        if key.strip().lower() == prop_name.lower():
            cleaned = value.strip()
            if cleaned:
                return cleaned
    return None


def _compact_html(html: str, max_chars: int = 140) -> str:
    compact = re.sub(r"\s+", " ", html or "").strip()
    if len(compact) <= max_chars:
        return compact
    return compact


def _clean_sentence(text: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    compact = re.sub(r"^fix any of the following:\s*", "", compact, flags=re.IGNORECASE)
    return compact[:240] if compact else ""
