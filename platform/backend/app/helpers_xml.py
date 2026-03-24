"""3-pass XML intelligence pipeline for reducing AI prompt token usage.

Pass 1: Strip non-selector attributes (60-70% reduction)
Pass 2: Keep only actionable elements (90% reduction)
Pass 3: Rank by relevance to test description (99% reduction)

SAFETY: Compose/SwiftUI detection must always run on RAW XML *before* filtering.
This module is a read-only prompt formatter — it never modifies stored XML.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

from .compose_detection import is_compose_screen
from .swiftui_detection import is_swiftui_screen

if TYPE_CHECKING:
    from .models import ScreenLibrary

# ---------------------------------------------------------------------------
# Pass 1 — Attribute sets to keep per platform
# ---------------------------------------------------------------------------

ANDROID_KEEP = {"resource-id", "content-desc", "text", "class", "clickable", "enabled"}
IOS_KEEP = {"name", "label", "value", "type", "enabled", "visible"}

# ---------------------------------------------------------------------------
# Pass 2 — Interactive element class sets
# ---------------------------------------------------------------------------

ANDROID_INTERACTIVE_CLASSES = {
    "android.widget.Button", "android.widget.EditText", "android.widget.CheckBox",
    "android.widget.RadioButton", "android.widget.Spinner", "android.widget.ImageButton",
    "android.widget.Switch", "android.widget.ToggleButton", "android.widget.TextView",
    "android.widget.ImageView",
}

IOS_INTERACTIVE_TYPES = {
    "XCUIElementTypeButton", "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField", "XCUIElementTypeSwitch",
    "XCUIElementTypeSlider", "XCUIElementTypeStaticText",
}

# Short name mappings for token-efficient output
_ANDROID_SHORT = {
    "android.widget.Button": "Button",
    "android.widget.EditText": "EditText",
    "android.widget.CheckBox": "CheckBox",
    "android.widget.RadioButton": "RadioButton",
    "android.widget.Spinner": "Spinner",
    "android.widget.ImageButton": "ImageButton",
    "android.widget.Switch": "Switch",
    "android.widget.ToggleButton": "ToggleButton",
    "android.widget.TextView": "TextView",
    "android.widget.ImageView": "ImageView",
    "android.view.View": "View",
}

_IOS_SHORT = {
    "XCUIElementTypeButton": "Button",
    "XCUIElementTypeTextField": "TextField",
    "XCUIElementTypeSecureTextField": "SecureTextField",
    "XCUIElementTypeSwitch": "Switch",
    "XCUIElementTypeSlider": "Slider",
    "XCUIElementTypeStaticText": "StaticText",
    "XCUIElementTypeImage": "Image",
    "XCUIElementTypeOther": "Other",
}


_INPUT_KEYWORDS = frozenset(["enter", "type", "input", "fill", "write", "submit", "form", "login", "sign"])

EDITABLE_ANDROID = {"android.widget.EditText"}
EDITABLE_IOS = {"XCUIElementTypeTextField", "XCUIElementTypeSecureTextField"}


def _is_ios(platform: str) -> bool:
    return platform.lower() in ("ios_sim", "ios")


# ---------------------------------------------------------------------------
# Pass 1
# ---------------------------------------------------------------------------

def strip_attributes(element: ET.Element, platform: str) -> dict[str, str]:
    """Keep only selector-relevant attributes from an XML element."""
    keep = IOS_KEEP if _is_ios(platform) else ANDROID_KEEP
    return {k: v for k, v in element.attrib.items() if k in keep and v}


# ---------------------------------------------------------------------------
# Pass 2
# ---------------------------------------------------------------------------

def is_actionable(attrs: dict[str, str], platform: str, *, is_compose: bool = False, is_swiftui: bool = False) -> bool:
    """True if the element can be targeted by a test step."""
    if _is_ios(platform):
        return _is_actionable_ios(attrs, is_swiftui)
    if is_compose:
        return _is_actionable_compose(attrs)
    return _is_actionable_android(attrs)


def _is_actionable_android(attrs: dict[str, str]) -> bool:
    has_selector = bool(attrs.get("resource-id") or attrs.get("content-desc") or attrs.get("text"))
    if not has_selector:
        return False
    is_clickable = attrs.get("clickable") == "true"
    cls = attrs.get("class", "")
    return is_clickable or cls in ANDROID_INTERACTIVE_CLASSES


def _is_actionable_compose(attrs: dict[str, str]) -> bool:
    """Compose wraps everything in android.view.View — only selectors matter."""
    return bool(attrs.get("resource-id") or attrs.get("content-desc") or attrs.get("text"))


def _is_actionable_ios(attrs: dict[str, str], is_swiftui: bool) -> bool:
    etype = attrs.get("type", "")
    name = attrs.get("name", "")
    label = attrs.get("label", "")

    if is_swiftui:
        if name:
            return True
        if label and etype != "XCUIElementTypeOther":
            return True
        return False

    # UIKit
    if etype in IOS_INTERACTIVE_TYPES:
        return True
    return bool(name or label)


# ---------------------------------------------------------------------------
# Pass 2b — Contextual filters (FM-01, FM-02)
# ---------------------------------------------------------------------------

def filter_for_input_context(
    elements: list[dict[str, str]],
    description: str,
    platform: str,
) -> list[dict[str, str]]:
    """FM-01 fix: when description implies form input, strip non-editable elements.

    Keeps editable fields and clickable elements (buttons for form submission).
    Returns all elements unchanged if no input keyword is detected.
    """
    if not description or not any(k in description.lower() for k in _INPUT_KEYWORDS):
        return elements

    editable = EDITABLE_IOS if _is_ios(platform) else EDITABLE_ANDROID
    return [
        e for e in elements
        if e.get("class", "") in editable
        or e.get("type", "") in editable
        or e.get("clickable") == "true"
    ]


def promote_clickable_parents(
    elements: list[dict[str, str]],
    parent_map: dict[int, dict[str, str]],
) -> list[dict[str, str]]:
    """FM-02 fix: remove non-clickable children whose parent is clickable.

    ``parent_map`` maps element id(attrs) → parent attrs dict.
    When a non-clickable child has a clickable parent already in the result set,
    it is dropped so the AI targets the parent instead.
    """
    clickable_ids = {
        id(e) for e in elements if e.get("clickable") == "true"
    }
    result: list[dict[str, str]] = []
    for e in elements:
        if e.get("clickable") != "true":
            parent = parent_map.get(id(e))
            if parent is not None and id(parent) in clickable_ids:
                continue
        result.append(e)
    return result


# ---------------------------------------------------------------------------
# Pass 3 — Relevance scoring
# ---------------------------------------------------------------------------

def score_element(attrs: dict[str, str], description: str, platform: str) -> int:
    """Score an element's relevance to the test description."""
    if not description:
        return 0
    desc_words = set(re.findall(r"\w{3,}", description.lower()))
    if not desc_words:
        return 0
    score = 0

    if _is_ios(platform):
        selector_attrs = ("name", "label", "value")
    else:
        selector_attrs = ("text", "content-desc", "resource-id")

    for attr in selector_attrs:
        val = attrs.get(attr, "").lower()
        if not val:
            continue
        for word in desc_words:
            if word in val:
                score += 3
        # resource-id suffix match: "com.app:id/btn_sign_in" → "btn sign in"
        if attr == "resource-id" and "/" in val:
            suffix = val.split("/")[-1].replace("_", " ").replace("-", " ")
            for word in desc_words:
                if word in suffix:
                    score += 2

    cls = attrs.get("class", "") or attrs.get("type", "")
    if cls.endswith(("Button", "EditText", "TextField")):
        score += 1

    return score


def filter_by_relevance(
    elements: list[dict[str, str]],
    description: str,
    platform: str,
    max_elements: int = 20,
    min_score: int = 0,
) -> list[dict[str, str]]:
    """Keep top-N elements ranked by relevance to the test description."""
    if not description:
        return elements[:max_elements]

    scored = [(el, score_element(el, description, platform)) for el in elements]
    matched = [(el, s) for el, s in scored if s > min_score]
    unmatched = [(el, s) for el, s in scored if s <= min_score]

    matched.sort(key=lambda x: x[1], reverse=True)
    remaining_slots = max(0, max_elements - len(matched))

    result = [el for el, _ in matched]
    if remaining_slots > 0:
        unmatched.sort(key=lambda x: x[1], reverse=True)
        result.extend(el for el, _ in unmatched[:remaining_slots])

    return result[:max_elements]


def select_relevant_screens(
    screens: list[Any],
    description: str,
    max_screens: int = 4,
) -> list[Any]:
    """Pick only screens relevant to the FRD description."""
    if not description or not screens:
        return screens[:max_screens]

    desc_words = set(re.findall(r"\w{3,}", description.lower()))
    if not desc_words:
        return screens[:max_screens]

    scored: list[tuple[Any, int]] = []
    for screen in screens:
        name_words = set(re.findall(r"\w{3,}", (screen.name or "").lower()))
        name_overlap = len(desc_words & name_words) * 3

        xml = getattr(screen, "xml_snapshot", None) or ""
        xml_lower = xml.lower()
        element_hits = sum(1 for word in desc_words if word in xml_lower)

        scored.append((screen, name_overlap + element_hits))

    scored.sort(key=lambda x: x[1], reverse=True)
    result = [s for s, sc in scored[:max_screens] if sc > 0]
    return result or [scored[0][0]] if scored else []


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _short_class(cls: str, platform: str) -> str:
    if _is_ios(platform):
        return _IOS_SHORT.get(cls, cls.replace("XCUIElementType", "") if cls.startswith("XCUIElementType") else cls)
    return _ANDROID_SHORT.get(cls, cls.rsplit(".", 1)[-1] if "." in cls else cls)


def _format_element(attrs: dict[str, str], platform: str) -> str:
    """Pipe-delimited, token-efficient format for a single element."""
    cls = attrs.get("class", "") or attrs.get("type", "")
    parts = [_short_class(cls, platform)]

    if _is_ios(platform):
        for attr, label in (("name", "name"), ("label", "label"), ("value", "value")):
            if attrs.get(attr):
                parts.append(f'{label}: "{attrs[attr]}"')
    else:
        if attrs.get("resource-id"):
            parts.append(f"rid: {attrs['resource-id']}")
        if attrs.get("content-desc"):
            parts.append(f'desc: "{attrs["content-desc"]}"')
        if attrs.get("text"):
            parts.append(f'text: "{attrs["text"]}"')
        if attrs.get("clickable") == "true":
            parts.append("clickable")

    return "  " + " | ".join(parts)


# ---------------------------------------------------------------------------
# Main pipeline entry points
# ---------------------------------------------------------------------------

def preprocess_xml(
    raw_xml: str,
    platform: str,
    description: str = "",
    max_elements: int = 20,
    screen_name: str = "",
) -> str:
    """3-pass XML preprocessing pipeline.

    Returns a compact, pipe-delimited string suitable for AI prompts.
    Detection (Compose/SwiftUI) runs on raw XML before any filtering.
    """
    if not raw_xml or not raw_xml.strip():
        return ""

    # Detect framework on RAW xml (safety rule)
    is_compose = not _is_ios(platform) and is_compose_screen(raw_xml)
    is_swiftui_flag = _is_ios(platform) and is_swiftui_screen(raw_xml)

    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return f"[XML parse error for {screen_name}]" if screen_name else "[XML parse error]"

    total_count = 0
    actionable: list[dict[str, str]] = []
    parent_map: dict[int, dict[str, str]] = {}
    _el_attrs_cache: dict[ET.Element, dict[str, str]] = {}

    def _walk_with_parent(node: ET.Element, parent_attrs: dict[str, str] | None) -> None:
        nonlocal total_count
        total_count += 1
        attrs = strip_attributes(node, platform)
        if attrs and is_actionable(attrs, platform, is_compose=is_compose, is_swiftui=is_swiftui_flag):
            actionable.append(attrs)
            _el_attrs_cache[node] = attrs
            if parent_attrs is not None:
                parent_map[id(attrs)] = parent_attrs
        for child in node:
            _walk_with_parent(child, _el_attrs_cache.get(node, parent_attrs))

    _walk_with_parent(root, None)

    filtered = promote_clickable_parents(actionable, parent_map)
    if description:
        filtered = filter_for_input_context(filtered, description, platform)
        filtered = filter_by_relevance(filtered, description, platform, max_elements)
    else:
        filtered = filtered[:max_elements]

    strategy = "compose" if is_compose else "swiftui" if is_swiftui_flag else "native"
    plat_label = "ios" if _is_ios(platform) else "android"
    header = f"=== {screen_name} ({plat_label}/{strategy}) | {len(filtered)} elements (filtered from {total_count}) ===" if screen_name else f"=== ({plat_label}/{strategy}) | {len(filtered)} elements (filtered from {total_count}) ==="

    lines = [_format_element(a, platform) for a in filtered]
    return header + "\n" + "\n".join(lines)


def build_xml_context_v2(
    screens: list[Any],
    description: str = "",
    max_screens: int = 4,
    max_elements_per_screen: int = 20,
) -> str:
    """Replacement for build_xml_context() with relevance filtering.

    Screens are pre-filtered by relevance, then each screen's XML
    is processed through the 3-pass pipeline.
    """
    if not screens:
        return ""

    selected = select_relevant_screens(screens, description, max_screens) if description else screens[:max_screens]

    chunks: list[str] = []
    for screen in selected:
        xml = getattr(screen, "xml_snapshot", None) or ""
        platform = getattr(screen, "platform", "android") or "android"
        chunk = preprocess_xml(
            xml,
            platform,
            description=description,
            max_elements=max_elements_per_screen,
            screen_name=getattr(screen, "name", ""),
        )
        if chunk:
            chunks.append(chunk)

    return "\n\n".join(chunks)


def preprocess_live_xml(
    raw_xml: str,
    platform: str,
    description: str = "",
    max_elements: int = 30,
) -> str:
    """Process live driver.page_source() XML.

    Replaces the current page_source_xml[:8000] truncation.
    Uses a slightly higher max_elements since there's only one screen.
    """
    return preprocess_xml(raw_xml, platform, description=description, max_elements=max_elements)
