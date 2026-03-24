"""Fallback extractor: scans AI-generated steps for hardcoded test data
and replaces with ${variable} references.

Used when the AI ignores the instruction to return a test_data block.
"""
from __future__ import annotations

import re
from typing import Any

DATA_PATTERNS: dict[str, str] = {
    "email": r"[\w.+-]+@[\w.-]+\.\w{2,}",
    "phone": r"[+]?\d{10,15}",
    "otp": r"^\d{4,6}$",
    "url": r"https?://\S+",
    "amount": r"[\$₹€£]\s*\d+[\d,.]*",
}


def _detect_data_type(value: str, step: dict[str, Any], prefix: str = "") -> str | None:
    """Classify a value as extractable test data. Returns a variable name or None."""
    v = value.strip()
    if not v or len(v) < 3:
        return None

    if re.match(r"[\w.+-]+@[\w.-]+\.\w{2,}$", v):
        return f"{prefix}email"
    if re.match(r"[+]?\d{10,15}$", v.replace(" ", "")):
        return f"{prefix}phone"
    if re.match(r"^\d{4,6}$", v):
        return f"{prefix}otp"
    if re.match(r"https?://", v):
        return f"{prefix}url"

    sel_val = (step.get("selector", {}).get("value") or "").lower()
    for kw in ("password", "username", "name", "address", "city", "pin", "email", "phone"):
        if kw in sel_val and len(v) >= 3:
            return f"{prefix}{kw}"

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
            # Skip if already uses ${var} syntax
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
