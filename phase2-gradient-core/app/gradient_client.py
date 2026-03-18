from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


@dataclass
class GradientResponse:
    text: str
    trace_id: str
    model_id: str
    latency_ms: int
    token_usage: dict[str, int] | None
    cost_usd: float | None


class GradientInferenceClient:
    provider = "digitalocean-gradient"
    _failure_markers = (
        "missing required digitalocean gradient",
        "cannot perform",
        "status\": \"failed\"",
        "status\":\"failed\"",
        "unable to",
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _chat_completions_url(self) -> str:
        base = self._settings.gradient_base_url.rstrip("/")

        if base.endswith("/chat/completions"):
            return base

        # Agent endpoints use /api/v1/chat/completions?agent=true.
        if "agents.do-ai.run" in base and "/api/v1" not in base:
            return f"{base}/api/v1/chat/completions?agent=true"

        if base.endswith("/v1") or base.endswith("/api/v1"):
            return f"{base}/chat/completions"

        return f"{base}/v1/chat/completions"

    async def infer(
        self,
        *,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        image_base64: str | None = None,
        request_timeout_seconds: int | None = None,
    ) -> GradientResponse:
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        if image_base64:
            payload["messages"].append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Element screenshot for visual analysis."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                        },
                    ],
                }
            )

        start = time.perf_counter()
        endpoint = self._chat_completions_url()
        timeout_seconds = request_timeout_seconds or self._settings.gradient_request_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {self._settings.gradient_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        latency_ms = int((time.perf_counter() - start) * 1000)
        response.raise_for_status()
        body = response.json()

        message: str = ""
        choices = body.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            if isinstance(content, str):
                message = content
            elif isinstance(content, list):
                pieces: list[str] = []
                for item in content:
                    if isinstance(item, str):
                        pieces.append(item)
                    elif isinstance(item, dict):
                        if isinstance(item.get("text"), str):
                            pieces.append(item["text"])
                        elif isinstance(item.get("content"), str):
                            pieces.append(item["content"])
                message = "\n".join(pieces)

        usage = body.get("usage")
        token_usage = None
        if isinstance(usage, dict):
            token_usage = {
                "input": int(usage.get("prompt_tokens", 0)),
                "output": int(usage.get("completion_tokens", 0)),
                "total": int(usage.get("total_tokens", 0)),
            }

        trace_id = (
            response.headers.get("x-gradient-trace-id")
            or response.headers.get("x-request-id")
            or response.headers.get("request-id")
            or body.get("trace_id")
            or f"local-{int(time.time() * 1000)}"
        )

        return GradientResponse(
            text=message.strip(),
            trace_id=trace_id,
            model_id=model_id,
            latency_ms=latency_ms,
            token_usage=token_usage,
            cost_usd=body.get("cost_usd"),
        )

    @staticmethod
    def _sanitize_html_for_prompt(raw_html: str, max_chars: int = 3000) -> str:
        html = raw_html or ""
        html = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
        html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        html = re.sub(r"<svg\b[^>]*>.*?</svg>", "<svg>[omitted]</svg>", html, flags=re.IGNORECASE | re.DOTALL)
        html = re.sub(r"data:[^\"'\\s>]+", "data:[omitted]", html, flags=re.IGNORECASE)
        html = re.sub(r"\s+", " ", html).strip()
        if len(html) > max_chars:
            return html[:max_chars].rstrip() + " ...[truncated]"
        return html

    @staticmethod
    def _sanitize_text_for_prompt(value: str, max_chars: int) -> str:
        compact = re.sub(r"\s+", " ", value or "").strip()
        if len(compact) > max_chars:
            return compact[:max_chars].rstrip() + " ...[truncated]"
        return compact

    @staticmethod
    def _strip_fences(text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        return cleaned.strip()

    @classmethod
    def _pdf_document_guidance(cls, page_text_hint: str, rule_context: str) -> str:
        combined = f"{page_text_hint} {rule_context}".lower()
        identifier = cls._extract_pdf_identifier(page_text_hint)

        if any(keyword in combined for keyword in ("reservation", "booking", "invoice", "check in", "check out")):
            title = (
                f"Reservation Invoice - {identifier}"
                if identifier
                else "Reservation Invoice"
            )
            return (
                "Detected document type: reservation/invoice.\n"
                "Prefer operator-facing fixes such as: set PDF title, set document language, tag Reservation Details and Payment Details sections, and ensure tabular invoice data uses header/data cell semantics.\n"
                f"Good suggestion example for a title field: {title}\n"
                "If dates, totals, reservation IDs, or contact blocks are present, use them in concrete example suggestions."
            )

        if any(keyword in combined for keyword in ("receipt", "subtotal", "tax", "payment", "amount due")):
            return (
                "Detected document type: receipt/payment document.\n"
                "Prefer fixes such as: set receipt title metadata, tag line-item tables, preserve currency/date text accurately, and expose payment/contact links with readable link text."
            )

        if any(keyword in combined for keyword in ("application", "form", "signature", "checkbox", "radio button", "fill out")):
            return (
                "Detected document type: form.\n"
                "Prefer fixes such as: give each field a programmatic name, bind labels to fields, define tab order, and provide concrete suggested field names based on visible labels."
            )

        return (
            "Document type not confidently detected.\n"
            "Prefer concise remediation with concrete examples whenever the OCR hints contain titles, identifiers, dates, monetary values, or section labels."
        )

    @staticmethod
    def _extract_pdf_identifier(text: str) -> str | None:
        patterns = (
            r"(?:reservation|booking|invoice|confirmation)\s*(?:id|number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9-]{4,})",
            r"#\s*([A-Z0-9-]{4,})",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    @classmethod
    def _extract_json_fragment(cls, text: str) -> Any | None:
        cleaned = cls._strip_fences(text)
        if not cleaned:
            return None
        try:
            return json.loads(cleaned)
        except Exception:
            pass

        for start, end in (("{", "}"), ("[", "]")):
            start_idx = cleaned.find(start)
            end_idx = cleaned.rfind(end)
            if start_idx >= 0 and end_idx > start_idx:
                fragment = cleaned[start_idx : end_idx + 1]
                try:
                    return json.loads(fragment)
                except Exception:
                    continue
        return None

    @classmethod
    def _find_string_by_keys(cls, obj: Any, keys: tuple[str, ...]) -> str | None:
        if isinstance(obj, dict):
            for key in keys:
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in obj.values():
                candidate = cls._find_string_by_keys(value, keys)
                if candidate:
                    return candidate
        elif isinstance(obj, list):
            for item in obj:
                candidate = cls._find_string_by_keys(item, keys)
                if candidate:
                    return candidate
        return None

    @classmethod
    def _has_failed_status(cls, obj: Any) -> bool:
        if isinstance(obj, dict):
            status = obj.get("status")
            if isinstance(status, str) and status.strip().lower() in {"failed", "error"}:
                return True
            for value in obj.values():
                if cls._has_failed_status(value):
                    return True
        elif isinstance(obj, list):
            for item in obj:
                if cls._has_failed_status(item):
                    return True
        return False

    @classmethod
    def _extract_html_candidate(cls, text: str) -> str | None:
        cleaned = cls._strip_fences(text)
        parsed = cls._extract_json_fragment(cleaned)
        if parsed is not None:
            html_from_json = cls._find_string_by_keys(
                parsed,
                (
                    "correctedHtml",
                    "corrected_html",
                    "proposedHtml",
                    "proposed_html",
                    "fixedHtml",
                    "remediationHtml",
                    "remediatedHtml",
                    "html",
                ),
            )
            if html_from_json and "<" in html_from_json and ">" in html_from_json:
                return cls._strip_fences(html_from_json)

        if "<" in cleaned and ">" in cleaned:
            match = re.search(r"(<[a-zA-Z][\s\S]*>)", cleaned)
            if match:
                return match.group(1).strip()
        return None

    @classmethod
    def normalize_coder_output(cls, response_text: str) -> tuple[str | None, str | None]:
        text = (response_text or "").strip()
        if not text:
            return None, "Model returned empty remediation output."

        parsed = cls._extract_json_fragment(text)
        html_candidate = cls._extract_html_candidate(text)
        if html_candidate:
            return html_candidate, None

        if parsed is not None and cls._has_failed_status(parsed):
            error_text = cls._find_string_by_keys(parsed, ("error", "message", "reason", "details"))
            return None, error_text or "Model returned a failed remediation payload."

        lowered = text.lower()
        if any(marker in lowered for marker in cls._failure_markers):
            return None, cls._sanitize_text_for_prompt(text, 500)

        return None, "No corrected HTML found in remediation response."

    @classmethod
    def normalize_alt_output(cls, response_text: str) -> tuple[str | None, str | None]:
        text = (response_text or "").strip()
        if not text:
            return None, "Model returned empty alt-text output."

        parsed = cls._extract_json_fragment(text)
        if parsed is not None:
            alt_candidate = cls._find_string_by_keys(
                parsed,
                ("altText", "alt_text", "proposedAltText", "alt", "description", "text"),
            )
            if alt_candidate:
                text = alt_candidate.strip()
            elif cls._has_failed_status(parsed):
                error_text = cls._find_string_by_keys(parsed, ("error", "message", "reason", "details"))
                return None, error_text or "Model returned a failed alt-text payload."

        if "<" in text and ">" in text:
            alt_match = re.search(r"""alt\s*=\s*["']([^"']+)["']""", text, flags=re.IGNORECASE)
            if alt_match:
                text = alt_match.group(1).strip()

        text = cls._strip_fences(text).strip().strip("'").strip('"')
        text = re.sub(r"^alt\s*=\s*", "", text, flags=re.IGNORECASE).strip()

        lowered = text.lower()
        if any(marker in lowered for marker in cls._failure_markers):
            return None, cls._sanitize_text_for_prompt(text, 500)

        if not text:
            return None, "No alt text found in vision response."

        return text[:200], None

    async def generate_alt_text(self, screenshot_base64: str, raw_html: str, failure_summary: str) -> GradientResponse:
        compact_html = self._sanitize_html_for_prompt(raw_html, max_chars=1200)
        compact_failure = self._sanitize_text_for_prompt(failure_summary, max_chars=600)
        try:
            return await self.infer(
                model_id=self._settings.gradient_vision_model_id,
                system_prompt=(
                    "You are an accessibility specialist. Return one concise WCAG-compliant alt text string only."
                ),
                user_prompt=(
                    "Analyze this failing image context and return one alt attribute value only.\n"
                    "Do not output JSON, markdown, labels, or additional commentary.\n"
                    f"HTML snippet: {compact_html}\n"
                    f"Failure summary: {compact_failure}"
                ),
                image_base64=screenshot_base64,
                request_timeout_seconds=min(30, self._settings.gradient_request_timeout_seconds),
            )
        except httpx.HTTPError:
            # Fallback for deployments that reject image payloads on chat endpoints.
            return await self.infer(
                model_id=self._settings.gradient_vision_model_id,
                system_prompt=(
                    "You are an accessibility specialist. Return one concise WCAG-compliant alt text string only."
                ),
                user_prompt=(
                    "Image payload could not be processed; infer best alt text from HTML context and"
                    " failure summary only. Output one alt attribute value only.\n"
                    "Do not output JSON, markdown, labels, or additional commentary.\n"
                    f"HTML snippet: {compact_html}\n"
                    f"Failure summary: {compact_failure}"
                ),
                image_base64=None,
            )

    async def rewrite_html(self, raw_html: str, failure_summary: str, rule_id: str) -> GradientResponse:
        compact_html = self._sanitize_html_for_prompt(raw_html, max_chars=3000)
        compact_failure = self._sanitize_text_for_prompt(failure_summary, max_chars=900)
        return await self.infer(
            model_id=self._settings.gradient_coder_model_id,
            system_prompt=(
                "You are GPT-5.3-Codex focused on WCAG remediation."
                " Return only corrected raw HTML with no JSON, markdown, or explanation."
            ),
            user_prompt=(
                "Rewrite the HTML snippet to satisfy the accessibility failure.\n"
                "Output only valid corrected HTML for this element/snippet.\n"
                f"Rule: {rule_id}\n"
                f"Failure summary: {compact_failure}\n"
                f"Broken HTML snippet: {compact_html}"
            ),
        )

    async def rewrite_html_batch(self, items: list[dict[str, Any]], rule_id: str) -> GradientResponse:
        compact_items = []
        for item in items:
            compact_items.append({
                "index": item["index"],
                "targetSelector": item.get("targetSelector"),
                "failureSummary": self._sanitize_text_for_prompt(str(item.get("failureSummary") or ""), max_chars=700),
                "rawHtml": self._sanitize_html_for_prompt(str(item.get("rawHtml") or ""), max_chars=1800),
            })

        return await self.infer(
            model_id=self._settings.gradient_coder_model_id,
            system_prompt=(
                "You are GPT-5.3-Codex focused on WCAG remediation."
                " Return JSON only."
            ),
            user_prompt=(
                "Rewrite each HTML snippet to satisfy the accessibility failure.\n"
                "Return JSON with this shape only:\n"
                "{\"results\": [{\"index\": number, \"correctedHtml\": string}]}\n"
                "Do not omit any item. Do not include markdown or commentary.\n"
                f"Rule: {rule_id}\n"
                f"Items: {json.dumps(compact_items, ensure_ascii=True)}"
            ),
        )

    async def analyze_pdf_page(self, page_text_hint: str, rule_context: str) -> GradientResponse:
        compact_hint = self._sanitize_text_for_prompt(page_text_hint, max_chars=2000)
        compact_context = self._sanitize_text_for_prompt(rule_context, max_chars=600)
        document_guidance = self._pdf_document_guidance(compact_hint, compact_context)
        return await self.infer(
            model_id=self._settings.gradient_pdf_model_id,
            system_prompt=(
                "You evaluate PDF accessibility findings under PDF/UA and WCAG mappings."
                " Return concise, operator-friendly remediation as JSON only."
            ),
            user_prompt=(
                "Given OCR/text hints from a PDF page or document, produce JSON with this shape only:\n"
                "{"
                "\"summary\": string,"
                "\"actions\": [{\"title\": string, \"fix\": string, \"suggestion\": string}],"
                "\"wcagMappings\": [{\"wcag\": string, \"rationale\": string}],"
                "\"limitations\": [string],"
                "\"expectedOutcome\": string"
                "}\n"
                "Keep the summary under 70 words. Keep actions concrete and implementation-focused."
                " When possible, include a short suggested value or example in `suggestion`."
                " Prioritize exact fixes a content editor or accessibility operator can apply immediately."
                " Do not include markdown fences or extra commentary.\n"
                f"Context: {compact_context}\n"
                f"Page text hint: {compact_hint}\n"
                f"Document guidance: {document_guidance}"
            ),
        )

    @classmethod
    def normalize_pdf_output(cls, response_text: str) -> tuple[str | None, dict[str, Any] | None, str | None]:
        text = (response_text or "").strip()
        if not text:
            return None, None, "Model returned empty PDF remediation output."

        parsed = cls._extract_json_fragment(text)
        if isinstance(parsed, dict):
            summary = cls._find_string_by_keys(parsed, ("summary", "message", "description", "text"))
            details: dict[str, Any] = {}

            actions = parsed.get("actions")
            if isinstance(actions, list):
                normalized_actions = []
                for action in actions:
                    if isinstance(action, dict):
                        title = cls._find_string_by_keys(action, ("title", "name", "label"))
                        fix = cls._find_string_by_keys(action, ("fix", "action", "description", "text"))
                        suggestion = cls._find_string_by_keys(action, ("suggestion", "example", "recommendedValue", "sample"))
                        if title or fix:
                            normalized_actions.append({
                                "title": title or "Recommended fix",
                                "fix": fix or title or "",
                                "suggestion": suggestion or "",
                            })
                    elif isinstance(action, str) and action.strip():
                        normalized_actions.append({
                            "title": "Recommended fix",
                            "fix": action.strip(),
                            "suggestion": "",
                        })
                if normalized_actions:
                    details["actions"] = normalized_actions

            mappings = parsed.get("wcagMappings")
            if isinstance(mappings, list):
                normalized_mappings = []
                for mapping in mappings:
                    if isinstance(mapping, dict):
                        wcag = cls._find_string_by_keys(mapping, ("wcag", "name", "criterion"))
                        rationale = cls._find_string_by_keys(mapping, ("rationale", "reason", "description"))
                        if wcag or rationale:
                            normalized_mappings.append({
                                "wcag": wcag or "WCAG mapping",
                                "rationale": rationale or "",
                            })
                if normalized_mappings:
                    details["wcagMappings"] = normalized_mappings

            limitations = parsed.get("limitations")
            if isinstance(limitations, list):
                normalized_limitations = [str(item).strip() for item in limitations if str(item).strip()]
                if normalized_limitations:
                    details["limitations"] = normalized_limitations

            expected = cls._find_string_by_keys(parsed, ("expectedOutcome", "outcome"))
            if expected:
                details["expectedOutcome"] = expected

            if summary:
                return summary, details or None, None

        lowered = text.lower()
        if any(marker in lowered for marker in cls._failure_markers):
            return None, None, cls._sanitize_text_for_prompt(text, 500)

        return cls._sanitize_text_for_prompt(text, 400), None, None

    @classmethod
    def normalize_coder_batch_output(
        cls,
        response_text: str,
        expected_count: int,
    ) -> tuple[dict[int, str], str | None]:
        text = (response_text or "").strip()
        if not text:
            return {}, "Model returned empty batch remediation output."

        parsed = cls._extract_json_fragment(text)
        if not isinstance(parsed, (dict, list)):
            return {}, "Batch remediation response was not valid JSON."

        items = parsed.get("results") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            return {}, "Batch remediation response did not contain a results array."

        normalized: dict[int, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            index_value = item.get("index")
            if not isinstance(index_value, int):
                continue
            corrected_html = cls._find_string_by_keys(
                item,
                (
                    "correctedHtml",
                    "corrected_html",
                    "proposedHtml",
                    "html",
                ),
            )
            if corrected_html:
                normalized[index_value] = corrected_html

        if len(normalized) < expected_count:
            return normalized, "Batch remediation response was incomplete."

        return normalized, None
