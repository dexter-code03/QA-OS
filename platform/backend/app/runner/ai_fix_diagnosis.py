"""
Structured failure classification for AI fix (pre-Gemini). Complements tap_debugger
with explicit causes and mandatory prompt rules (Compose / UiAutomator, anti-timeout-only).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from ..swiftui_detection import is_swiftui_screen
from .tap_debugger import _bool_attr, _strategy_matches_node, _walk

# Causes referenced by system prompt RULES
CAUSE_ELEMENT_NOT_IN_XML = "ELEMENT_NOT_IN_XML"
CAUSE_ELEMENT_NOT_DISPLAYED = "ELEMENT_NOT_DISPLAYED"
CAUSE_COMPOSE_ID_UNRELIABLE = "COMPOSE_ID_SELECTOR_UNRELIABLE"
CAUSE_STALE_OR_STRATEGY = "STALE_OR_STRATEGY_MISMATCH"
CAUSE_UNKNOWN = "UNKNOWN"
CAUSE_SWIFTUI_SELECTOR_UNRELIABLE = "SWIFTUI_SELECTOR_UNRELIABLE"
CAUSE_IOS_NO_ACCESSIBILITY_IDENTIFIER = "IOS_NO_ACCESSIBILITY_IDENTIFIER"


def parse_android_package(app_context: str) -> Optional[str]:
    """Extract applicationId from 'Display name · com.package' or any substring like com.foo.bar."""
    if not (app_context or "").strip():
        return None
    m = re.search(r"\b(com\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)\b", app_context, re.I)
    if m:
        return m.group(1)
    parts = [p.strip() for p in app_context.split("·") if p.strip()]
    if len(parts) >= 2:
        last = parts[-1]
        if re.match(r"^com\.[a-z][a-z0-9_.]+$", last, re.I):
            return last
    return None


def _error_hints(error_message: str) -> set[str]:
    e = (error_message or "").lower()
    hints: set[str] = set()
    if "timeout" in e or "timed out" in e or "waited" in e:
        hints.add("timeout")
    if "not displayed" in e or "not visible" in e or "hidden" in e:
        hints.add("not_visible")
    if "stale" in e:
        hints.add("stale")
    if "not interactable" in e or "unable to perform" in e:
        hints.add("interact")
    if "no such element" in e or "could not be located" in e or "unable to locate" in e:
        hints.add("missing")
    return hints


def _full_resource_id(android_package: Optional[str], selector_value: str, node_rid: str) -> Optional[str]:
    """Prefer node's resource-id; else build com.pkg:id/fragment from package + step value."""
    rid = (node_rid or "").strip()
    if rid:
        return rid
    v = (selector_value or "").strip()
    if not v:
        return None
    if ":" in v:
        return v
    if not android_package:
        return None
    tail = v.rsplit("/", 1)[-1]
    if tail.startswith("id/"):
        return f"{android_package}:{tail}"
    return f"{android_package}:id/{tail}"


def _page_looks_compose(xml_lower: str) -> bool:
    return "androidx.compose" in xml_lower or "composeview" in xml_lower


def build_failure_diagnosis_block(diagnosis: dict[str, Any]) -> str:
    lines = [f"Cause: {diagnosis.get('cause', CAUSE_UNKNOWN)}"]
    for e in diagnosis.get("evidence") or []:
        if e:
            lines.append(f"Evidence: {e}")
    if diagnosis.get("message"):
        lines.append(f"Message: {diagnosis['message']}")
    if diagnosis.get("recommended_fix"):
        lines.append(f"Recommended fix: {diagnosis['recommended_fix']}")
    if diagnosis.get("recommended_strategy"):
        lines.append(f"Recommended selector strategy: {diagnosis['recommended_strategy']}")
    if diagnosis.get("recommended_value"):
        lines.append(f"Recommended selector value: {diagnosis['recommended_value']}")
    return "\n".join(lines)


def _ios_id_appears_in_xml(xml: str, value: str) -> bool:
    if not value or not xml:
        return False
    if f'name="{value}"' in xml:
        return True
    if f'label="{value}"' in xml:
        return True
    return bool(re.search(rf'name\s*=\s*"{re.escape(value)}"', xml)) or bool(
        re.search(rf"label\s*=\s*'{re.escape(value)}'", xml)
    )


def _classify_failure_ios(
    failed_step: dict[str, Any],
    error_message: str,
    page_source_raw: str,
    page_source_fallback: str,
    tap_diagnosis: Optional[dict[str, Any]],
) -> dict[str, Any]:
    diagnosis: dict[str, Any] = {
        "cause": CAUSE_UNKNOWN,
        "evidence": [],
        "recommended_fix": None,
        "recommended_strategy": None,
        "recommended_value": None,
    }

    stype = (failed_step.get("type") or "").lower()
    if stype not in ("tap", "type", "waitforvisible", "assertvisible", "asserttext"):
        diagnosis["evidence"].append(f"Step type {stype!r} — structured locator classification skipped.")
        return diagnosis

    sel = failed_step.get("selector") or {}
    strategy = (sel.get("using") or "accessibilityId").strip()
    value = (sel.get("value") or "").strip()
    if not value:
        diagnosis["evidence"].append("No selector value on failed step.")
        return diagnosis

    xml = (page_source_raw or page_source_fallback or "").strip()
    if not xml:
        diagnosis["evidence"].append("No page source XML available for classification.")
        if tap_diagnosis:
            diagnosis["evidence"].append(f"Tap debugger: root_cause={tap_diagnosis.get('root_cause')}")
        return diagnosis

    hints = _error_hints(error_message)
    swiftui = is_swiftui_screen(xml)
    strat_l = strategy.lower().replace("_", "").replace("-", "").replace(" ", "")
    is_id_like = strat_l in ("id", "resourceid", "accessibilityid")

    if swiftui and is_id_like and not _ios_id_appears_in_xml(xml, value):
        diagnosis["cause"] = CAUSE_IOS_NO_ACCESSIBILITY_IDENTIFIER
        diagnosis["evidence"].append(
            "SwiftUI screen: selector value does not appear as name= or label= in page source — "
            "developers should add .accessibilityIdentifier() on the SwiftUI view."
        )
        diagnosis["recommended_fix"] = "dev_fix_required"
        diagnosis["message"] = (
            f"Cannot automate reliably without accessibility: ask dev to add .accessibilityIdentifier('{value}') "
            "or ensure label is stable."
        )
        return diagnosis

    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        diagnosis["evidence"].append("Page source is not strict XML — skipping ElementTree classification.")
        if tap_diagnosis:
            diagnosis["evidence"].append(
                f"Rely on TAP DEBUGGER: found={tap_diagnosis.get('found')} root_cause={tap_diagnosis.get('root_cause')}"
            )
        return diagnosis

    nodes = [n for n in _walk(root) if _strategy_matches_node(n.attrib, strategy, value)]

    if not nodes:
        diagnosis["cause"] = CAUSE_ELEMENT_NOT_IN_XML
        diagnosis["evidence"].append("No element node matches the failing selector in the current page source.")
        diagnosis["recommended_fix"] = "wrong_screen_or_navigation"
        if tap_diagnosis and tap_diagnosis.get("root_cause") in ("wrong_screen", "element_missing"):
            diagnosis["evidence"].append(f"Tap debugger agrees: {tap_diagnosis.get('root_cause_detail', '')}")
        return diagnosis

    n = nodes[0]
    attrib = n.attrib
    displayed = _bool_attr(attrib, "displayed", True)
    visible = _bool_attr(attrib, "visible", True)
    shown = displayed and visible
    enabled = _bool_attr(attrib, "enabled", True)
    name_attr = (attrib.get("name") or "").strip()
    label_attr = (attrib.get("label") or "").strip()
    if name_attr:
        pred_val = f'name == "{name_attr}"'
    elif label_attr:
        pred_val = f'label == "{label_attr}"'
    else:
        pred_val = ""

    if not shown:
        diagnosis["cause"] = CAUSE_ELEMENT_NOT_DISPLAYED
        diagnosis["evidence"].append("Matching node exists but is not visible in the hierarchy.")
        diagnosis["recommended_fix"] = "scroll_or_wait"
        return diagnosis

    if not enabled:
        diagnosis["evidence"].append("Element matches but enabled=false — may need different flow or wait.")
        return diagnosis

    if swiftui and is_id_like:
        diagnosis["cause"] = CAUSE_SWIFTUI_SELECTOR_UNRELIABLE
        diagnosis["evidence"].append("Page source suggests SwiftUI (many XCUIElementTypeOther / sparse identifiers).")
        diagnosis["evidence"].append("Standard id/accessibility id can be unreliable — prefer XCUITest predicate.")
        diagnosis["recommended_fix"] = "switch_to_ios_predicate"
        diagnosis["recommended_strategy"] = "-ios predicate string"
        if pred_val:
            diagnosis["recommended_value"] = pred_val
        return diagnosis

    if is_id_like and hints & {"stale", "interact", "timeout", "missing"} and shown:
        diagnosis["cause"] = CAUSE_STALE_OR_STRATEGY
        diagnosis["evidence"].append(
            "Element present and visible but error suggests stale element, interaction, or locate timeout."
        )
        diagnosis["recommended_fix"] = "switch_to_ios_predicate"
        diagnosis["recommended_strategy"] = "-ios predicate string"
        if pred_val:
            diagnosis["recommended_value"] = pred_val
        return diagnosis

    if tap_diagnosis:
        diagnosis["evidence"].append(
            f"Tap debugger: found={tap_diagnosis.get('found')} root_cause={tap_diagnosis.get('root_cause')}"
        )
    return diagnosis


def classify_failure_for_ai_fix(
    failed_step: dict[str, Any],
    error_message: str,
    page_source_raw: str,
    page_source_fallback: str,
    platform: str,
    android_package: Optional[str],
    tap_diagnosis: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """
    Return a JSON-serializable diagnosis dict for the LLM and API response.
    ``platform`` is automation target: android | ios_sim.
    """
    diagnosis: dict[str, Any] = {
        "cause": CAUSE_UNKNOWN,
        "evidence": [],
        "recommended_fix": None,
        "recommended_strategy": None,
        "recommended_value": None,
    }

    pf = (platform or "").lower().replace("-", "_")
    if pf in ("ios", "ios_sim"):
        return _classify_failure_ios(
            failed_step, error_message, page_source_raw, page_source_fallback, tap_diagnosis
        )

    if pf != "android":
        diagnosis["evidence"].append(f"Platform {platform!r} — classification tuned for Android.")
        return diagnosis

    stype = (failed_step.get("type") or "").lower()
    if stype not in ("tap", "type", "waitforvisible", "assertvisible", "asserttext"):
        diagnosis["evidence"].append(f"Step type {stype!r} — structured locator classification skipped.")
        return diagnosis

    sel = failed_step.get("selector") or {}
    strategy = (sel.get("using") or "accessibilityId").strip()
    value = (sel.get("value") or "").strip()
    if not value:
        diagnosis["evidence"].append("No selector value on failed step.")
        return diagnosis

    xml = (page_source_raw or page_source_fallback or "").strip()
    if not xml:
        diagnosis["evidence"].append("No page source XML available for classification.")
        if tap_diagnosis:
            diagnosis["evidence"].append(f"Tap debugger: root_cause={tap_diagnosis.get('root_cause')}")
        return diagnosis

    hints = _error_hints(error_message)
    xml_l = xml.lower()

    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        diagnosis["evidence"].append("Page source is not strict XML — skipping ElementTree classification.")
        if tap_diagnosis:
            diagnosis["evidence"].append(
                f"Rely on TAP DEBUGGER: found={tap_diagnosis.get('found')} root_cause={tap_diagnosis.get('root_cause')}"
            )
        return diagnosis

    nodes = [n for n in _walk(root) if _strategy_matches_node(n.attrib, strategy, value)]

    if not nodes:
        diagnosis["cause"] = CAUSE_ELEMENT_NOT_IN_XML
        diagnosis["evidence"].append("No element node matches the failing selector in the current page source.")
        diagnosis["recommended_fix"] = "wrong_screen_or_navigation"
        if tap_diagnosis and tap_diagnosis.get("root_cause") in ("wrong_screen", "element_missing"):
            diagnosis["evidence"].append(f"Tap debugger agrees: {tap_diagnosis.get('root_cause_detail', '')}")
        return diagnosis

    n = nodes[0]
    attrib = n.attrib
    displayed = _bool_attr(attrib, "displayed", True)
    enabled = _bool_attr(attrib, "enabled", True)
    node_rid = (attrib.get("resource-id") or "").strip()

    if not displayed:
        diagnosis["cause"] = CAUSE_ELEMENT_NOT_DISPLAYED
        diagnosis["evidence"].append("Matching node exists but displayed=false (or not shown in hierarchy).")
        diagnosis["recommended_fix"] = "scroll_or_wait"
        diagnosis["evidence"].append("Prefer swipe/scroll or waitForVisible before this step; do not only raise timeout.")
        return diagnosis

    if not enabled:
        diagnosis["evidence"].append("Element matches but enabled=false — may need different flow or wait.")
        return diagnosis

    strat_l = strategy.lower().replace("_", "").replace("-", "")
    is_id_like = strat_l in ("id", "resourceid")
    compose = _page_looks_compose(xml_l)

    full_rid = _full_resource_id(android_package, value, node_rid)
    uia_value = f'new UiSelector().resourceId("{full_rid}")' if full_rid else None

    if compose and is_id_like:
        diagnosis["cause"] = CAUSE_COMPOSE_ID_UNRELIABLE
        diagnosis["evidence"].append("Page source suggests Jetpack Compose (androidx.compose / ComposeView).")
        diagnosis["evidence"].append("Standard Appium id/resource-id locator is often unreliable on Compose.")
        diagnosis["recommended_fix"] = "switch_to_uiautomator"
        diagnosis["recommended_strategy"] = "-android uiautomator"
        if uia_value:
            diagnosis["recommended_value"] = uia_value
        else:
            diagnosis["evidence"].append("Could not derive full resource-id — use resource-id from XML in UiSelector.")
        return diagnosis

    if is_id_like and hints & {"stale", "interact", "timeout", "missing"} and displayed:
        diagnosis["cause"] = CAUSE_STALE_OR_STRATEGY
        diagnosis["evidence"].append(
            "Element present and displayed=true but error suggests stale element, interaction, or locate timeout."
        )
        diagnosis["recommended_fix"] = "switch_to_uiautomator"
        diagnosis["recommended_strategy"] = "-android uiautomator"
        if uia_value:
            diagnosis["recommended_value"] = uia_value
        return diagnosis

    if tap_diagnosis:
        diagnosis["evidence"].append(
            f"Tap debugger: found={tap_diagnosis.get('found')} root_cause={tap_diagnosis.get('root_cause')}"
        )
    return diagnosis


AI_FIX_CLASSIFICATION_RULES = """
STRUCTURED FAILURE CLASSIFICATION — MANDATORY RULES:
1. Read the section "=== STRUCTURED FAILURE CLASSIFICATION ===" in the user message. It states a confirmed cause from server-side analysis of the page source and failing step.
2. If Cause is COMPOSE_ID_SELECTOR_UNRELIABLE or STALE_OR_STRATEGY_MISMATCH (Android):
   - You MUST change the failing step's selector to use strategy "-android uiautomator" (or equivalent Appium JSON: using "-android uiautomator").
   - Use the Recommended selector value exactly when provided (UiSelector().resourceId(...)).
   - Do NOT fix the failure by only increasing wait/ms or timeout — that is not sufficient for these causes.
3. If Cause is SWIFTUI_SELECTOR_UNRELIABLE or STALE_OR_STRATEGY_MISMATCH on iOS:
   - Prefer "-ios predicate string" with the Recommended selector value when provided (e.g. name == "id").
   - If still ambiguous, use "-ios class chain" to narrow (e.g. **/XCUIElementTypeButton[`name == 'x'`]).
   - Do NOT fix by only increasing wait/ms — insufficient when classification says switch strategy.
4. If Cause is IOS_NO_ACCESSIBILITY_IDENTIFIER:
   - Do not invent locators — state that the developer must add .accessibilityIdentifier(...) or stable labels in SwiftUI.
5. If Cause is ELEMENT_NOT_DISPLAYED:
   - Add a scroll/swipe or waitForVisible before the failing step; keep added waits at most 10000ms unless clearly justified.
   - Do not only bump timeout on the same tap.
6. If Cause is ELEMENT_NOT_IN_XML:
   - The app is likely on the wrong screen — adjust earlier steps or add navigation; do NOT invent a new selector for an element absent from the XML.
7. If Cause is UNKNOWN:
   - Timeout increases are allowed only as a last resort when no better explanation exists.
Still obey all other rules (keyboard actions, acceptance_criteria, complete fixed_steps JSON).
"""
