"""Extractor: scans AI-generated steps for hardcoded test data
and replaces with ${variable} references.

Runs on EVERY AI response (not just as a fallback).
"""
from __future__ import annotations

import re
from typing import Any

_NAVIGATION_WORDS = frozenset({
    "up", "down", "left", "right", "return", "done", "go", "next", "search",
    "send", "back", "home", "enter", "delete", "tab", "escape",
})

_SELECTOR_KEYWORD_TO_VAR: dict[str, str] = {
    "password": "password",
    "username": "username",
    "name": "name",
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone": "phone",
    "address": "address",
    "city": "city",
    "pin": "pin",
    "zip": "zipcode",
    "otp": "otp",
    "code": "code",
    "amount": "amount",
    "card": "card_number",
    "cvv": "cvv",
    "expir": "expiry",
    "search": "search_query",
    "comment": "comment",
    "message": "message",
    "description": "description",
}


def _detect_data_type(value: str, step: dict[str, Any], prefix: str = "") -> str | None:
    """Classify a value as extractable test data. Returns a variable name or None."""
    v = value.strip()
    if not v or len(v) < 3:
        return None

    if v.lower() in _NAVIGATION_WORDS:
        return None

    if re.match(r"[\w.+-]+@[\w.-]+\.\w{2,}$", v):
        return f"{prefix}email"
    if re.match(r"[+]?\d{10,15}$", v.replace(" ", "")):
        return f"{prefix}phone"
    if re.match(r"^\d{4,6}$", v):
        return f"{prefix}otp"
    if re.match(r"https?://", v):
        return f"{prefix}url"
    if re.match(r"[\$₹€£]\s*\d+[\d,.]*", v):
        return f"{prefix}amount"

    sel_val = (step.get("selector", {}).get("value") or "").lower()
    step_desc = (step.get("description") or "").lower()
    combined_context = f"{sel_val} {step_desc}"
    for kw, var_name in _SELECTOR_KEYWORD_TO_VAR.items():
        if kw in combined_context and len(v) >= 3:
            return f"{prefix}{var_name}"

    if step.get("type") in ("type", "clearAndType") and len(v) >= 4 and "${" not in v:
        if re.match(r"^[\d]+$", v) and len(v) >= 4:
            return f"{prefix}numeric_value"
        if re.search(r"[A-Z]", v) and re.search(r"[a-z]", v) and len(v) >= 6:
            return f"{prefix}input_value"
        if " " in v and len(v) >= 5:
            return f"{prefix}input_text"

    return None


def extract_variables_from_steps(
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Scan steps for hardcoded data, extract into variables.

    Returns (modified_steps with ${vars}, extracted_variables dict).
    """
    variables: dict[str, str] = {}
    modified: list[dict[str, Any]] = []
    used_names: set[str] = set()

    def _unique_name(base: str) -> str:
        if base not in used_names:
            used_names.add(base)
            return base
        i = 2
        while f"{base}_{i}" in used_names:
            i += 1
        name = f"{base}_{i}"
        used_names.add(name)
        return name

    for step in steps:
        s = dict(step)
        text = s.get("text", "")

        if text and s.get("type") in ("type", "clearAndType"):
            if "${" not in text:
                var_name = _detect_data_type(text, s)
                if var_name:
                    var_name = _unique_name(var_name)
                    variables[var_name] = text
                    s["text"] = f"${{{var_name}}}"

        expect = s.get("expect", "")
        if expect and s.get("type") in ("assertText", "assertTextContains"):
            if "${" not in expect:
                var_name = _detect_data_type(expect, s, prefix="expected_")
                if var_name:
                    var_name = _unique_name(var_name)
                    variables[var_name] = expect
                    s["expect"] = f"${{{var_name}}}"

        modified.append(s)

    return modified, variables


def enforce_data_layer(
    steps: list[dict[str, Any]],
    existing_test_data: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Always-run enforcement: scan AI output and extract any missed hardcoded data.

    Merges with existing test_data from the AI response.
    Returns (cleaned_steps, merged_test_data).
    """
    cleaned_steps, extracted = extract_variables_from_steps(steps)
    merged = dict(existing_test_data or {})
    merged.update(extracted)
    return cleaned_steps, merged
