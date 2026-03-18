from __future__ import annotations

import re
import uuid
from typing import Any

from .contracts import AxeNode, FixResult


def apply_coder_heuristic(rule_id: str, node: AxeNode) -> FixResult | None:
    rule = (rule_id or "").strip().lower()
    html = node.rawHtml or ""

    if rule == "color-contrast":
        corrected = _fix_color_contrast(html, node.failureSummary)
        if corrected:
            return _build_result(
                rule_id=rule_id,
                target_selector=node.targetSelector,
                corrected_html=corrected,
                confidence=0.72,
                rationale="Applied a rule-specific contrast heuristic before model remediation.",
            )

    if rule == "nested-interactive":
        corrected = _fix_nested_interactive(html)
        if corrected and corrected != html:
            return _build_result(
                rule_id=rule_id,
                target_selector=node.targetSelector,
                corrected_html=corrected,
                confidence=0.84,
                rationale="Applied a structural heuristic to remove outer interactive semantics from a nested interactive wrapper.",
            )

    if rule == "aria-prohibited-attr":
        corrected = _fix_aria_prohibited_attr(html)
        if corrected and corrected != html:
            return _build_result(
                rule_id=rule_id,
                target_selector=node.targetSelector,
                corrected_html=corrected,
                confidence=0.76,
                rationale="Applied a rule-specific heuristic to remove prohibited ARIA attributes from the element.",
            )

    return None


def _build_result(
    *,
    rule_id: str,
    target_selector: str,
    corrected_html: str,
    confidence: float,
    rationale: str,
) -> FixResult:
    return FixResult(
        ruleId=rule_id,
        targetSelector=target_selector,
        assignedAgent="coder_node",
        status="success",
        proposedHtml=corrected_html,
        confidence=confidence,
        rationale=rationale,
        traceId=f"heuristic-{uuid.uuid4()}",
        modelId="heuristic-fast-path",
        latencyMs=0,
    )


def _fix_nested_interactive(html: str) -> str | None:
    if not re.search(r"<(?:a|button|input|select|textarea)\b", html, flags=re.IGNORECASE):
        return None

    match = re.match(r"\s*<(?P<tag>[a-zA-Z0-9:-]+)(?P<attrs>[^>]*)>", html)
    if not match:
        return None

    attrs = match.group("attrs")
    cleaned_attrs = attrs
    for attr in (
        "role",
        "tabindex",
        "onclick",
        "onkeypress",
        "onkeydown",
        "onkeyup",
        "aria-pressed",
        "aria-expanded",
        "aria-haspopup",
        "aria-label",
        "aria-labelledby",
        "aria-describedby",
    ):
        cleaned_attrs = re.sub(
            rf"""\s+{attr}\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""",
            "",
            cleaned_attrs,
            flags=re.IGNORECASE,
        )

    cleaned_attrs = re.sub(r"\s+", " ", cleaned_attrs).rstrip()
    replacement = f"<{match.group('tag')}{cleaned_attrs}>"
    return replacement + html[match.end() :]


def _fix_aria_prohibited_attr(html: str) -> str | None:
    match = re.match(r"\s*<(?P<tag>[a-zA-Z0-9:-]+)(?P<attrs>[^>]*)>", html)
    if not match:
        return None

    attrs = match.group("attrs")
    cleaned_attrs = attrs
    removed = False
    for attr in (
        "aria-label",
        "aria-labelledby",
        "aria-roledescription",
        "aria-braillelabel",
        "aria-brailleroledescription",
    ):
        next_attrs, count = re.subn(
            rf"""\s+{attr}\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""",
            "",
            cleaned_attrs,
            flags=re.IGNORECASE,
        )
        if count:
            removed = True
        cleaned_attrs = next_attrs

    if not removed:
        return None

    cleaned_attrs = re.sub(r"\s+", " ", cleaned_attrs).rstrip()
    replacement = f"<{match.group('tag')}{cleaned_attrs}>"
    return replacement + html[match.end() :]


def _fix_color_contrast(html: str, failure_summary: str) -> str | None:
    match = re.match(r"\s*<(?P<tag>[a-zA-Z0-9:-]+)(?P<attrs>[^>]*)>", html)
    if not match:
        return None

    attrs = match.group("attrs")
    background = _extract_named_hex("background color", failure_summary)
    foreground = _extract_named_hex("foreground color", failure_summary) or "#777777"
    desired_color = _choose_accessible_text_color(foreground, background or "#ffffff")
    style_updates = {"color": desired_color}

    tag = match.group("tag").lower()
    if tag == "a":
        style_updates["text-decoration"] = "underline"

    updated_attrs = _set_style_properties(attrs, style_updates)
    replacement = f"<{match.group('tag')}{updated_attrs}>"
    return replacement + html[match.end() :]


def _extract_named_hex(label: str, text: str) -> str | None:
    match = re.search(
        rf"{re.escape(label)}\s*:\s*(#[0-9a-fA-F]{{3,8}})",
        text or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return _normalize_hex(match.group(1))


def _normalize_hex(color: str) -> str:
    color = color.strip()
    if len(color) == 4:
        return "#" + "".join(ch * 2 for ch in color[1:])
    if len(color) >= 7:
        return color[:7].lower()
    return color.lower()


def _choose_accessible_text_color(foreground: str, background: str) -> str:
    background = _normalize_hex(background)
    foreground = _normalize_hex(foreground)

    best = foreground
    best_ratio = _contrast_ratio(foreground, background)
    if best_ratio >= 4.5:
        return foreground

    black_ratio = _contrast_ratio("#000000", background)
    white_ratio = _contrast_ratio("#ffffff", background)
    if max(black_ratio, white_ratio) >= 4.5:
        return "#000000" if black_ratio >= white_ratio else "#ffffff"

    for step in range(1, 21):
        candidate = _mix_colors(foreground, "#000000", step / 20)
        ratio = _contrast_ratio(candidate, background)
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
        if ratio >= 4.5:
            return candidate

    return best


def _set_style_properties(attrs: str, updates: dict[str, str]) -> str:
    style_match = re.search(r"""style\s*=\s*(["'])(.*?)\1""", attrs, flags=re.IGNORECASE | re.DOTALL)
    styles = _parse_style_declarations(style_match.group(2) if style_match else "")
    for key, value in updates.items():
        styles[key.lower()] = value

    style_value = "; ".join(f"{key}: {value}" for key, value in styles.items()).strip()
    if style_value and not style_value.endswith(";"):
        style_value += ";"

    if style_match:
        return (
            attrs[: style_match.start()]
            + f'style="{style_value}"'
            + attrs[style_match.end() :]
        )

    suffix = "" if not attrs or attrs.endswith(" ") else " "
    return f'{attrs}{suffix}style="{style_value}"'


def _parse_style_declarations(style_value: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for chunk in style_value.split(";"):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            declarations[key] = value
    return declarations


def _mix_colors(color_a: str, color_b: str, ratio: float) -> str:
    a = _hex_to_rgb(color_a)
    b = _hex_to_rgb(color_b)
    mixed = tuple(
        max(0, min(255, round((1 - ratio) * comp_a + ratio * comp_b)))
        for comp_a, comp_b in zip(a, b, strict=False)
    )
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _contrast_ratio(foreground: str, background: str) -> float:
    fg = _relative_luminance(_hex_to_rgb(foreground))
    bg = _relative_luminance(_hex_to_rgb(background))
    lighter = max(fg, bg)
    darker = min(fg, bg)
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for component in rgb:
        normalized = component / 255
        if normalized <= 0.03928:
            channels.append(normalized / 12.92)
        else:
            channels.append(((normalized + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    normalized = _normalize_hex(color).lstrip("#")
    return (
        int(normalized[0:2], 16),
        int(normalized[2:4], 16),
        int(normalized[4:6], 16),
    )
