"""Heuristics to detect SwiftUI-heavy iOS XCUITest hierarchy XML."""

from __future__ import annotations

import re


def is_swiftui_screen(page_source: str) -> bool:
    """
    SwiftUI often produces many XCUIElementTypeOther nodes and sparse `name` attributes
    compared to UIKit-typed controls.
    """
    if not page_source or len(page_source) < 80:
        return False

    others = len(re.findall(r"XCUIElementTypeOther", page_source))
    total = len(re.findall(r"XCUIElementType\w+", page_source))
    high_other_ratio = total > 0 and others / total > 0.4

    name_attrs = re.findall(r'\bname="[^"]+"', page_source)
    low_name_ratio = total > 0 and len(name_attrs) / total < 0.3

    return high_other_ratio or low_name_ratio
