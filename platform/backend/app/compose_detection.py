"""Heuristics to detect Jetpack Compose-heavy Android hierarchy XML."""

from __future__ import annotations

import re


def is_compose_screen(page_source: str) -> bool:
    """
    Return True when page source likely represents a Compose UI.

    Uses Compose class/package hints and a low resource-id density heuristic
    (Compose nodes often lack stable resource-ids compared to clickable nodes).
    """
    if not page_source or len(page_source) < 80:
        return False

    compose_signals = (
        "ComposeView",
        "androidx.compose",
        "androidx.compose.ui",
    )
    signal_count = sum(1 for s in compose_signals if s in page_source)

    resource_ids = re.findall(r'resource-id="[^"]+"', page_source)
    clickable_elements = re.findall(r'clickable="true"', page_source)
    n_click = len(clickable_elements)
    low_resource_id_ratio = n_click > 0 and len(resource_ids) / max(n_click, 1) < 0.5

    return signal_count >= 1 or low_resource_id_ratio
