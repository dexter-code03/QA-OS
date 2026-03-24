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
CAUSE_KEYBOARD_COVERING = "KEYBOARD_COVERING_ELEMENT"
CAUSE_ELEMENT_OFF_SCREEN = "ELEMENT_OFF_SCREEN"
CAUSE_WRAPPER_NOT_EDITABLE = "WRAPPER_NOT_EDITABLE"


def keyboard_likely_visible(step_results: list[dict[str, Any]]) -> bool:
    """True if a type/clearAndType step ran within the last 3 steps — keyboard may be covering."""
    recent = step_results[-3:] if len(step_results) >= 3 else step_results
    for r in recent:
        stype = (r.get("type") or r.get("step", {}).get("type") or "").lower()
        if stype in ("type", "clearandtype"):
            return True
    return False


def _xml_has_scrollable(xml: str) -> bool:
    return 'scrollable="true"' in xml or "ScrollView" in xml


_EDITABLE_CLASSES = {
    "android.widget.EditText",
    "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField",
}


def _detect_wrapper_with_child_input(
    node: ET.Element,
    platform: str,
    android_package: Optional[str] = None,
) -> Optional[dict[str, str]]:
    """If ``node`` is a non-editable wrapper with a child EditText/TextField, return the recommended selector.

    Returns None if node IS an editable element or has no editable children.
    """
    node_cls = node.attrib.get("class", "") or node.attrib.get("type", "")
    if node_cls in _EDITABLE_CLASSES:
        return None

    for child in node:
        child_cls = child.attrib.get("class", "") or child.attrib.get("type", "")
        if child_cls not in _EDITABLE_CLASSES:
            continue
        # Found a child editable element — build a recommended selector
        node_rid = (node.attrib.get("resource-id") or "").strip()
        if node_rid and child_cls == "android.widget.EditText":
            rid = node_rid
            if android_package and ":" not in rid:
                rid = f"{android_package}:id/{rid}"
            return {
                "strategy": "-android uiautomator",
                "value": f'new UiSelector().resourceId("{rid}").childSelector(new UiSelector().className("android.widget.EditText"))',
                "evidence": f"Element '{node_rid}' is a wrapper (class={node_cls}) with a child EditText. "
                            f"type/clearAndType must target the child EditText, not the wrapper.",
            }
        child_name = child.attrib.get("name", "") or child.attrib.get("label", "")
        if child_name:
            return {
                "strategy": "-ios predicate string" if "ios" in platform.lower() else "-android uiautomator",
                "value": f'name == "{child_name}"' if "ios" in platform.lower() else f'new UiSelector().className("{child_cls}")',
                "evidence": f"Element is a wrapper (class={node_cls}) with a child {child_cls}. "
                            f"type/clearAndType must target the child input, not the wrapper.",
            }
        return {
            "strategy": "-android uiautomator" if "android" in platform.lower() else "xpath",
            "value": f'new UiSelector().className("{child_cls}")' if "android" in platform.lower() else f'//{child_cls}',
            "evidence": f"Element is a wrapper (class={node_cls}) with a child {child_cls}. "
                        f"type/clearAndType must target the child input, not the wrapper.",
        }

    return None


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
    if "cannot set the element" in e or "invalidelementstate" in e or "did you interact with the correct element" in e:
        hints.add("invalid_element_state")
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

    # FM-06: wrapper-not-editable on iOS
    if stype in ("type", "clearandtype"):
        wrapper_info = _detect_wrapper_with_child_input(n, "ios")
        if wrapper_info:
            diagnosis["cause"] = CAUSE_WRAPPER_NOT_EDITABLE
            diagnosis["evidence"].append(wrapper_info["evidence"])
            diagnosis["recommended_fix"] = "target_child_input"
            diagnosis["recommended_strategy"] = wrapper_info["strategy"]
            diagnosis["recommended_value"] = wrapper_info["value"]
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
    step_results: list[dict[str, Any]] | None = None,
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

    xml = (page_source_raw or page_source_fallback or "").strip()
    stype = (failed_step.get("type") or "").lower()

    # FM-03: keyboard covering element — check before other causes
    if step_results and keyboard_likely_visible(step_results):
        hints = _error_hints(error_message)
        element_present = bool(xml) and bool(
            (failed_step.get("selector") or {}).get("value")
            and (failed_step.get("selector", {}).get("value", "") in xml)
        )
        if element_present and hints & {"timeout", "not_visible", "interact"}:
            diagnosis["cause"] = CAUSE_KEYBOARD_COVERING
            diagnosis["evidence"].append(
                "A type step ran within last 3 steps — keyboard likely covering this element."
            )
            diagnosis["recommended_fix"] = "add_hide_keyboard_before_step"
            return diagnosis

    # FM-06: InvalidElementStateException on type/clearAndType — proactive wrapper scan
    hints = _error_hints(error_message)
    if stype in ("type", "clearandtype") and "invalid_element_state" in hints and xml:
        try:
            root = ET.fromstring(xml)
            sel = failed_step.get("selector") or {}
            nodes = [n for n in _walk(root) if _strategy_matches_node(n.attrib, sel.get("using", ""), sel.get("value", ""))]
            if nodes:
                wrapper_info = _detect_wrapper_with_child_input(nodes[0], platform, android_package)
                if wrapper_info:
                    diagnosis["cause"] = CAUSE_WRAPPER_NOT_EDITABLE
                    diagnosis["evidence"].append(wrapper_info["evidence"])
                    diagnosis["evidence"].append(
                        f"Error: InvalidElementStateException — '{error_message[:120]}'"
                    )
                    diagnosis["recommended_fix"] = "target_child_input"
                    diagnosis["recommended_strategy"] = wrapper_info["strategy"]
                    diagnosis["recommended_value"] = wrapper_info["value"]
                    return diagnosis
        except ET.ParseError:
            pass

    # FM-05: element off-screen — scrollable context present
    if xml and _xml_has_scrollable(xml):
        if hints & {"timeout", "not_visible", "missing"} and stype in ("tap", "waitforvisible", "assertvisible"):
            sel_val = (failed_step.get("selector") or {}).get("value", "")
            if sel_val and sel_val not in xml:
                diagnosis["cause"] = CAUSE_ELEMENT_OFF_SCREEN
                diagnosis["evidence"].append(
                    "ScrollView detected and element not in current viewport — likely off-screen."
                )
                diagnosis["recommended_fix"] = "scroll_before_step"
                return diagnosis

    pf = (platform or "").lower().replace("-", "_")
    if pf in ("ios", "ios_sim"):
        return _classify_failure_ios(
            failed_step, error_message, page_source_raw, page_source_fallback, tap_diagnosis
        )

    if pf != "android":
        diagnosis["evidence"].append(f"Platform {platform!r} — classification tuned for Android.")
        return diagnosis

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

    # FM-06: wrapper-not-editable — type/clearAndType targeting a container instead of child EditText
    if stype in ("type", "clearandtype"):
        wrapper_info = _detect_wrapper_with_child_input(n, "android", android_package)
        if wrapper_info:
            diagnosis["cause"] = CAUSE_WRAPPER_NOT_EDITABLE
            diagnosis["evidence"].append(wrapper_info["evidence"])
            diagnosis["recommended_fix"] = "target_child_input"
            diagnosis["recommended_strategy"] = wrapper_info["strategy"]
            diagnosis["recommended_value"] = wrapper_info["value"]
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
7. If Cause is KEYBOARD_COVERING_ELEMENT:
   - Add a hideKeyboard step immediately before the failing step. The software keyboard is covering the target element.
   - Do NOT increase timeout or change the selector — the element exists but is physically obscured.
8. If Cause is ELEMENT_OFF_SCREEN:
   - Add a scroll/swipe step before the failing step to bring it into view. The page is scrollable and the element is below the viewport.
   - Do NOT only bump timeout.
9. If Cause is WRAPPER_NOT_EDITABLE:
   - The selector targets a WRAPPER/CONTAINER element, NOT the actual editable input inside it.
   - You MUST use the Recommended selector value which targets the child input element.
   - Android: use .childSelector(new UiSelector().className("android.widget.EditText")) appended to the parent's UiSelector.
   - iOS: target XCUIElementTypeTextField or XCUIElementTypeSecureTextField child directly.
   - Also split the step: tap the wrapper first, then type into the child EditText, then hideKeyboard.
   - Do NOT keep targeting the wrapper — that will always fail with InvalidElementStateException.
10. If Cause is UNKNOWN:
    - Timeout increases are allowed only as a last resort when no better explanation exists.
Still obey all other rules (keyboard actions, acceptance_criteria, complete fixed_steps JSON).
"""
