"""Variable resolution engine for data-driven testing.

Resolves ${variable_name} placeholders in test step fields using a layered
context: row variables > data set variables > project default > built-ins.
"""

from __future__ import annotations

import copy
import random
import re
import time
from datetime import datetime


_VAR_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_.\-]*)}")

BUILTINS = {
    "__timestamp": lambda **_: str(int(time.time())),
    "__random": lambda **_: str(random.randint(10000, 99999)),
    "__run_id": lambda run_id=0, **_: str(run_id),
    "__platform": lambda platform="android", **_: platform,
    "__date": lambda **_: datetime.now().strftime("%Y-%m-%d"),
}


def resolve_variables(text: str, context: dict[str, str]) -> str:
    """Replace ${var} placeholders with values from context.

    Unresolved variables are left as-is so they're visible in step output.
    """
    if "${" not in text:
        return text

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        if key in context:
            return str(context[key])
        return match.group(0)

    return _VAR_PATTERN.sub(_replace, text)


def resolve_step(raw_step: dict, context: dict[str, str]) -> dict:
    """Deep-resolve all ${var} placeholders in a step dict."""
    step = copy.deepcopy(raw_step)
    if step.get("text") and isinstance(step["text"], str):
        step["text"] = resolve_variables(step["text"], context)
    if step.get("expect") and isinstance(step["expect"], str):
        step["expect"] = resolve_variables(step["expect"], context)
    if step.get("selector") and isinstance(step["selector"], dict):
        val = step["selector"].get("value")
        if val and isinstance(val, str):
            step["selector"]["value"] = resolve_variables(val, context)
    if step.get("meta") and isinstance(step["meta"], dict):
        for k, v in step["meta"].items():
            if isinstance(v, str):
                step["meta"][k] = resolve_variables(v, context)
    return step


def build_context(
    *,
    data_set_variables: dict | None = None,
    data_set_rows: list[dict] | None = None,
    row_index: int | None = None,
    run_id: int = 0,
    platform: str = "android",
) -> dict[str, str]:
    """Build the layered variable context for a run.

    Priority (highest wins):
    1. Row variables (from data-driven row)
    2. Data set variables (key-value pairs)
    3. Built-in variables
    """
    ctx: dict[str, str] = {}

    # Built-ins (lowest priority)
    for key, fn in BUILTINS.items():
        ctx[key] = fn(run_id=run_id, platform=platform)

    # Data set variables
    if data_set_variables:
        for k, v in data_set_variables.items():
            ctx[k] = str(v)

    # Row variables (highest priority)
    if data_set_rows and row_index is not None and 0 <= row_index < len(data_set_rows):
        for k, v in data_set_rows[row_index].items():
            ctx[k] = str(v)

    return ctx
