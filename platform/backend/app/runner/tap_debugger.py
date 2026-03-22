"""Heuristic diagnosis of tap / visibility failures from Appium page source XML."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

# Root cause codes consumed by API + frontend
RC_WRONG_SELECTOR = "wrong_selector"
RC_TIMING = "timing_race"
RC_SCROLLED = "scrolled_off"
RC_OVERLAY = "overlay_blocking"
RC_DISABLED = "element_disabled"
RC_WRONG_SCREEN = "wrong_screen"
RC_MISSING = "element_missing"
RC_XML_PARSE = "xml_parse_failed"


@dataclass
class SelectorSuggestion:
    strategy: str
    value: str
    score: int
    label: str


@dataclass
class TapDiagnosis:
    found: bool
    root_cause: str
    root_cause_detail: str
    is_clickable: bool
    is_visible: bool
    recommended_wait_ms: int
    suggestions: list[SelectorSuggestion] = field(default_factory=list)


def _bool_attr(attrib: dict[str, str], key: str, default: bool = True) -> bool:
    v = attrib.get(key)
    if v is None:
        return default
    return str(v).lower() in ("true", "1", "yes")


def _walk(el: ET.Element) -> list[ET.Element]:
    out: list[ET.Element] = [el]
    for c in el:
        out.extend(_walk(c))
    return out


def _norm_rid(rid: str) -> str:
    rid = (rid or "").strip()
    if "/" in rid:
        rid = rid.rsplit("/", 1)[-1]
    return rid


def _strategy_matches_node(attrib: dict[str, str], strategy: str, value: str) -> bool:
    s = (strategy or "accessibilityId").lower().replace("_", "").replace("-", "")
    val = (value or "").strip()
    if not val:
        return False
    if s in ("id", "resourceid"):
        rid = attrib.get("resource-id") or attrib.get("name") or ""
        return rid == val or _norm_rid(rid) == _norm_rid(val) or val in rid
    if s in ("accessibilityid", "contentdesc"):
        return (attrib.get("content-desc") or "").strip() == val or (attrib.get("name") or "").strip() == val
    if s == "class":
        name = (attrib.get("class") or attrib.get("type") or "").strip()
        return val in name or name.endswith(val)
    if s == "xpath":
        # Light extraction from common xpath patterns
        m = re.search(r"@resource-id\s*=\s*[\"']([^\"']+)[\"']", value)
        if m and _strategy_matches_node(attrib, "id", m.group(1)):
            return True
        m = re.search(r"@content-desc\s*=\s*[\"']([^\"']+)[\"']", value)
        if m and _strategy_matches_node(attrib, "accessibilityId", m.group(1)):
            return True
        m = re.search(r"@text\s*=\s*[\"']([^\"']+)[\"']", value)
        if m and (attrib.get("text") or "").strip() == m.group(1):
            return True
        return val in "".join(f"{k}={v}" for k, v in attrib.items())
    # name / partial
    if s == "name":
        return (attrib.get("name") or "").strip() == val
    return False


def _collect_suggestions(attrib: dict[str, str], value: str) -> list[SelectorSuggestion]:
    """Rank alternative locators for a node that relates to ``value``."""
    out: list[SelectorSuggestion] = []
    rid = (attrib.get("resource-id") or "").strip()
    if rid:
        out.append(SelectorSuggestion("id", rid, 92, "resource-id"))
    cd = (attrib.get("content-desc") or "").strip()
    if cd:
        out.append(SelectorSuggestion("accessibilityId", cd, 88, "content-desc"))
    tx = (attrib.get("text") or "").strip()
    if tx and tx != value:
        out.append(SelectorSuggestion("accessibilityId", tx, 70 if len(tx) < 40 else 55, "text"))
    nm = (attrib.get("name") or "").strip()
    if nm and nm not in {cd, tx, rid}:
        out.append(SelectorSuggestion("accessibilityId", nm, 85, "name"))
    # Dedupe by (strategy, value), keep best score
    best: dict[tuple[str, str], SelectorSuggestion] = {}
    for sug in out:
        key = (sug.strategy, sug.value)
        if key not in best or sug.score > best[key].score:
            best[key] = sug
    return sorted(best.values(), key=lambda x: -x.score)


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


def _detect_overlay(nodes: list[ET.Element]) -> bool:
    """True if page source suggests a blocking dialog / sheet."""
    for el in nodes[:800]:
        tag = (el.tag or "").lower()
        cls = (el.attrib.get("class") or el.attrib.get("type") or "").lower()
        if "dialog" in cls or "alert" in cls or "popup" in cls or "sheet" in cls:
            return True
        rid = (el.attrib.get("resource-id") or "").lower()
        if "progress" in rid or "loading" in rid or "spinner" in rid:
            if _bool_attr(el.attrib, "displayed", True):
                return True
    return False


def diagnose_tap_failure(
    strategy: str,
    value: str,
    page_source_xml: str,
    step_index: int,
    all_steps: list[dict[str, Any]],
    step_results: list[dict[str, Any]],
) -> TapDiagnosis:
    """
    Inspect XML and the failing locator to infer root cause and suggest locators.

    ``all_steps`` / ``step_results`` provide light context (e.g. prior failures, waits).
    """
    if not page_source_xml or not (value or "").strip():
        return TapDiagnosis(
            False,
            RC_MISSING,
            "No page source or selector value to diagnose.",
            False,
            False,
            0,
            [],
        )
    try:
        root = ET.fromstring(page_source_xml.strip())
    except ET.ParseError:
        return TapDiagnosis(
            False,
            RC_XML_PARSE,
            "Page source could not be parsed as strict XML (often unescaped & or < in attributes). "
            "Use raw Appium hierarchy for tap diagnosis, not a simplified export.",
            False,
            False,
            0,
            [],
        )

    nodes = _walk(root)
    err = ""
    if 0 <= step_index < len(step_results):
        d = step_results[step_index].get("details", "")
        if isinstance(d, dict):
            err = str(d.get("error") or d)
        else:
            err = str(d)
    hints = _error_hints(err)

    current_hits = [n for n in nodes if _strategy_matches_node(n.attrib, strategy, value)]
    overlayish = _detect_overlay(nodes)

    # Nodes that "contain" the searched token (likely same element, different attribute)
    val_l = value.strip().lower()
    fuzzy_nodes: list[ET.Element] = []
    for n in nodes[:1500]:
        a = n.attrib
        pool = " ".join(
            [
                a.get("resource-id", ""),
                a.get("content-desc", ""),
                a.get("text", ""),
                a.get("name", ""),
                a.get("label", ""),
                a.get("value", ""),
            ]
        ).lower()
        if val_l and val_l in pool:
            fuzzy_nodes.append(n)

    suggestions: list[SelectorSuggestion] = []
    is_clickable = False
    is_visible = True
    found = False
    root_cause = RC_MISSING
    root_detail = "Element not found with current strategy; no strong match in page source."
    recommended = 0

    if current_hits:
        found = True
        ch = current_hits[0]
        is_clickable = _bool_attr(ch.attrib, "clickable", False)
        is_visible = _bool_attr(ch.attrib, "displayed", True) and _bool_attr(ch.attrib, "visible", True)
        en = _bool_attr(ch.attrib, "enabled", True)
        if not en:
            root_cause = RC_DISABLED
            root_detail = "Element matches the locator but is disabled (enabled=false)."
        elif not is_visible and hints & {"timeout", "not_visible", "missing"}:
            root_cause = RC_TIMING
            root_detail = "Element exists in the hierarchy but was not visible/displayed when located."
            recommended = 3000
        elif not is_visible:
            root_cause = RC_SCROLLED
            root_detail = "Element is in the hierarchy but not in the visible viewport."
            recommended = 0
        elif overlayish and not is_clickable and (strategy or "").lower() == "tap":
            root_cause = RC_OVERLAY
            root_detail = "Another surface (dialog/loading) may be blocking interaction."
        elif hints & {"stale", "interact"}:
            root_cause = RC_TIMING
            root_detail = "Stale or non-interactable state — often timing or animation."
            recommended = 2500
        elif hints & {"not_visible"}:
            root_cause = RC_TIMING
            root_detail = "Driver reported visibility issues while the element exists in XML."
            recommended = 2000
        else:
            root_cause = RC_WRONG_SCREEN
            root_detail = "Locator matched an element; failure may be wrong screen/state or strict visibility."
        suggestions = _collect_suggestions(ch.attrib, value)[:5]
    elif fuzzy_nodes:
        found = True
        root_cause = RC_WRONG_SELECTOR
        pick = fuzzy_nodes[0]
        is_clickable = _bool_attr(pick.attrib, "clickable", False)
        is_visible = _bool_attr(pick.attrib, "displayed", True)
        root_detail = (
            f"Nothing matched {strategy}='{value}', but similar text/id appears elsewhere in the tree. "
            "Try a different locator attribute."
        )
        suggestions = _collect_suggestions(pick.attrib, value)[:5]
        if overlayish:
            root_cause = RC_OVERLAY
            root_detail += " An overlay/dialog may also be present."
    else:
        found = False
        root_cause = RC_MISSING
        root_detail = "No element in page source matches this selector or its text/id fragments."
        if len(nodes) < 8:
            root_detail += " Very small hierarchy — app may be on an unexpected or blank screen."
            root_cause = RC_WRONG_SCREEN

    # Prior step: no wait before quick tap sequence
    if root_cause in (RC_MISSING, RC_TIMING) and step_index > 0:
        prev = all_steps[step_index - 1] if step_index - 1 < len(all_steps) else {}
        if (prev.get("type") not in ("wait", "waitForVisible")) and recommended == 0 and hints & {"timeout", "missing"}:
            recommended = max(recommended, 2000)

    if root_cause == RC_WRONG_SELECTOR and recommended == 0 and hints & {"timeout"}:
        recommended = 1500

    return TapDiagnosis(
        found=found,
        root_cause=root_cause,
        root_cause_detail=root_detail,
        is_clickable=is_clickable,
        is_visible=is_visible,
        recommended_wait_ms=recommended,
        suggestions=suggestions[:5],
    )
