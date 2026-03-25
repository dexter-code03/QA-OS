from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from typing import Any, Optional

logger = logging.getLogger(__name__)

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..ai_rules import UNIVERSAL_RULES, build_rules_block
from ..compose_detection import is_compose_screen
from ..db import SessionLocal
from ..helpers import (
    ai_creds,
    android_selector_generation_rules,
    build_xml_context,
    compress_screenshot,
    filter_screen_library_by_build,
    gemini_extract_text,
    ios_selector_generation_rules,
    load_settings,
)
from ..helpers_data_extraction import enforce_data_layer
from ..helpers_xml import build_xml_context_v2, preprocess_live_xml, sanitize_selector_packages, validate_selectors_against_xml
from ..models import Build, Project, ScreenLibrary, TestDefinition, TestSuite
from ..runner.ai_fix_diagnosis import (
    AI_FIX_CLASSIFICATION_RULES,
    build_failure_diagnosis_block,
    check_screen_identity,
    classify_failure_for_ai_fix,
    parse_android_package,
)
from ..runner.tap_debugger import diagnose_tap_failure
from ..settings import settings
from ..swiftui_detection import is_swiftui_screen

router = APIRouter()


def _variable_hint(project_id: Optional[int]) -> str:
    """Build a hint about available ${variables} for the AI to use."""
    if not project_id:
        return ""
    from ..models import DataSet
    with SessionLocal() as db:
        sets = db.query(DataSet).filter(DataSet.project_id == project_id).limit(10).all()
    if not sets:
        return ""
    all_keys: set[str] = set()
    for ds in sets:
        if ds.variables:
            all_keys.update(ds.variables.keys())
        if ds.rows:
            for row in ds.rows[:1]:
                all_keys.update(row.keys())
    if not all_keys:
        return ""
    keys_preview = ", ".join(sorted(all_keys)[:20])
    return (
        "\n\nDATA VARIABLES: The project has test data with these variable names: "
        + keys_preview
        + '\nUse ${variable_name} syntax in step text/expect fields instead of hardcoded values '
        "when appropriate. E.g. ${username}, ${password}.\n"
    )

def _figma_hint() -> str:
    """Fetch Figma component names and format as a hint for the AI."""
    try:
        s = load_settings()
        token = (s.get("figma_token") or "").strip()
        file_key = (s.get("figma_file_key") or "").strip()
        if not token or not file_key:
            return ""
        from .integrations import _cached_figma_component_names, _figma_components_ttl_bucket
        bucket = _figma_components_ttl_bucket()
        names = list(_cached_figma_component_names(token, file_key, bucket))
        if not names:
            return ""
        preview = ", ".join(names[:30])
        return (
            "\n\nFIGMA COMPONENT NAMES (design intent — use for naming and understanding UI structure, NOT as selectors):\n"
            f"{preview}\n"
            "These are the developer's intended component names. Use them to understand what each UI element represents "
            "and to generate meaningful step descriptions and test case names.\n"
        )
    except Exception:
        return ""


_rate_buckets: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 10
_RATE_WINDOW = 60  # seconds


def _check_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    _rate_buckets[ip] = bucket = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(bucket) >= _RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Max 10 AI requests per minute.",
        )
    bucket.append(now)


def _build_gemini_body(
    system_prompt: str,
    user_msg: str,
    images: list[tuple[str, str]] | None = None,
    temperature: float = 0.1,
    max_images: int = 6,
) -> dict[str, Any]:
    """Build Gemini API request body with proper system_instruction separation."""
    parts: list[dict[str, Any]] = [{"text": user_msg}]
    for img_name, img_b64 in (images or [])[:max_images]:
        parts.append({"text": f"\n[Screenshot: {img_name}]"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})
    return {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }


def _load_screen_context(
    project_id: int | None,
    folder_id: int | None,
    screen_names: list[str],
    build_ids: list[int] | None,
    build_id: int | None,
    platform: str,
    description: str = "",
    max_screens: int = 4,
    max_elements_per_screen: int = 35,
) -> tuple[str, list[tuple[str, str]], list[ScreenLibrary], list[str]]:
    """Load screen library context: xml_context, images, screen objects, raw XMLs."""
    xml_context = ""
    screen_images: list[tuple[str, str]] = []
    screens_for_prompt: list[ScreenLibrary] = []
    raw_xmls: list[str] = []

    if not project_id or not (folder_id or screen_names):
        return xml_context, screen_images, screens_for_prompt, raw_xmls

    with SessionLocal() as db:
        if folder_id:
            q = db.query(ScreenLibrary).filter(
                ScreenLibrary.folder_id == folder_id,
                ScreenLibrary.platform == platform,
            )
        else:
            q = db.query(ScreenLibrary).filter(
                ScreenLibrary.project_id == project_id,
                ScreenLibrary.platform == platform,
                ScreenLibrary.name.in_(screen_names),
            )
        q = filter_screen_library_by_build(q, build_ids, build_id)
        screens = q.all()
        if screens:
            screens_for_prompt = list(screens)
            xml_context = build_xml_context_v2(
                screens, description=description,
                max_screens=max_screens,
                max_elements_per_screen=max_elements_per_screen,
            )
            for scr in screens:
                raw_xmls.append(getattr(scr, "xml_snapshot", "") or "")
                if scr.screenshot_path:
                    fpath = settings.artifacts_dir / str(scr.project_id) / scr.screenshot_path
                    if fpath.exists():
                        img_b64 = compress_screenshot(fpath)
                        if img_b64:
                            screen_images.append((scr.name, img_b64))

    return xml_context, screen_images, screens_for_prompt, raw_xmls


async def _gemini_call(
    api_key: str,
    model: str,
    body: dict[str, Any],
    timeout: int = 60,
) -> dict[str, Any]:
    """Make a Gemini API call and return parsed JSON."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=body, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"AI API error: {resp.text[:300]}")
        data = resp.json()
        text = gemini_extract_text(data)
        return json.loads(text)


# ── AI Step Generation ─────────────────────────────────────────────────


class GenerateStepsRequest(BaseModel):
    platform: str
    prompt: str
    page_source_xml: str = ""
    screen_names: list[str] = []
    folder_id: Optional[int] = None
    project_id: Optional[int] = None
    build_id: Optional[int] = None
    build_ids: Optional[list[int]] = None


@router.post("/api/ai/generate-steps")
async def generate_steps(request: Request, payload: GenerateStepsRequest) -> dict[str, Any]:
    _check_rate_limit(request)
    if payload.platform not in ("android", "ios_sim"):
        raise HTTPException(status_code=400, detail="platform must be android|ios_sim")

    s = load_settings()
    api_key, model = ai_creds(s)

    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    xml_context, screen_images, screens_for_prompt, raw_xmls = _load_screen_context(
        payload.project_id, payload.folder_id, payload.screen_names,
        payload.build_ids, payload.build_id, payload.platform, payload.prompt,
    )
    grounded = bool(xml_context)

    using_choices = (
        "accessibilityId|id|xpath|-android uiautomator"
        if payload.platform == "android"
        else "accessibilityId|id|xpath|-ios predicate string|-ios class chain"
    )

    if grounded and xml_context:
        android_rules = android_selector_generation_rules(screens_for_prompt) if payload.platform == "android" else ""
        ios_rules = ios_selector_generation_rules(screens_for_prompt) if payload.platform == "ios_sim" else ""

        _any_xml = "".join(getattr(sc, "xml_snapshot", "") or "" for sc in screens_for_prompt[:3])
        if payload.platform == "android" and _any_xml and is_compose_screen(_any_xml):
            _screen_type = "compose"
        elif payload.platform == "ios_sim" and _any_xml and is_swiftui_screen(_any_xml):
            _screen_type = "swiftui"
        else:
            _screen_type = "native"
        rules_block = build_rules_block(payload.prompt, xml_context, payload.platform, _screen_type)

        sel_json_key = '{"using":"' + using_choices + '","value":"..."}'
        system_prompt = (
            "You are a mobile QA automation expert generating Appium test steps.\n"
            "You will receive real XML page source from the app under test.\n"
            "You MUST use only selectors (resource-id, content-desc, text, class) that exist in the provided XML. Never invent selectors.\n"
            'Return ONLY valid JSON: {"steps": [{"type": "<step_type>",'
            ' "selector": ' + sel_json_key + ","
            ' "text": "...", "ms": 1000, "expect":"...", "meta": {...}}],'
            ' "test_data": {"variable_name": "value", ...}}\n'
            "IMPORTANT: Extract ALL test data (emails, phones, passwords, OTPs, URLs, names, amounts) into the test_data object. "
            "Use ${variable_name} syntax in step text/expect fields instead of hardcoded values.\n\n"
            "Available step types:\n"
            "  Tapping: tap, doubleTap, longPress, tapByCoordinates (meta.x, meta.y)\n"
            "  Text: type, clear, clearAndType\n"
            "  Wait: wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled\n"
            "  Gesture: swipe, scroll (text=direction; scroll optionally has selector to scroll-until-visible)\n"
            "  Assert: assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute (meta.attribute + expect)\n"
            "  Keyboard: pressKey (text=key), keyboardAction (legacy alias), hideKeyboard\n"
            "  App: launchApp, closeApp, resetApp (text=bundleId/package, optional)\n"
            "  Capture: takeScreenshot, getPageSource\n\n"
            + rules_block + "\n\n"
            + android_rules
            + ios_rules
            + "SELECTOR PRIORITY ORDER for native Android / iOS (use first match found in XML; skip for Compose-only steps where rules above say uiautomator):\n"
            "1. resource-id (most stable)\n"
            "2. content-desc / accessibility id (stable)\n"
            "3. text (fragile — only if no ID available)\n"
            "4. xpath (last resort — only if nothing else exists)\n"
            "IMPORTANT: For keyboard keys (return, done, go, next, search), use keyboardAction instead of tap.\n"
            "Use hideKeyboard when you need to dismiss the keyboard without pressing a specific key.\n"
            "Every selector you use must be found verbatim in the XML below.\n"
            "No markdown, no explanation, only JSON."
        )
        var_hint = _variable_hint(payload.project_id)
        figma_hint = _figma_hint()
        user_msg = f"Platform: {payload.platform}\nTest objective:\n{payload.prompt}{var_hint}{figma_hint}\n\nDOM CONTEXT\n==========\n{xml_context}"
    else:
        _live_xml = (payload.page_source_xml or "").strip()
        raw_xmls = [_live_xml] if _live_xml else []
        if payload.platform == "android" and _live_xml and is_compose_screen(payload.page_source_xml):
            _screen_type_ng = "compose"
        elif payload.platform == "ios_sim" and _live_xml and is_swiftui_screen(payload.page_source_xml):
            _screen_type_ng = "swiftui"
        else:
            _screen_type_ng = "native"

        live_xml_str = ""
        if _live_xml:
            live_xml_str = preprocess_live_xml(payload.page_source_xml, payload.platform, description=payload.prompt)

        rules_block_ng = build_rules_block(payload.prompt, live_xml_str, payload.platform, _screen_type_ng)

        sel_json_key_ng = '{"using":"' + using_choices + '","value":"..."}'
        system_prompt = (
            "You are a senior mobile QA automation engineer.\n"
            "Return ONLY valid JSON with this shape:\n"
            '{"steps": [{"type": "<step_type>",'
            ' "selector": ' + sel_json_key_ng + ","
            ' "text": "...", "ms": 1000, "expect":"...", "meta": {...}}],'
            ' "test_data": {"variable_name": "value", ...}}\n'
            "IMPORTANT: Extract ALL test data (emails, phones, passwords, OTPs, URLs, names, amounts) into the test_data object. "
            "Use ${variable_name} syntax in step text/expect fields instead of hardcoded values.\n"
            "Available step types:\n"
            "  Tapping: tap, doubleTap, longPress, tapByCoordinates (meta.x, meta.y)\n"
            "  Text: type, clear, clearAndType\n"
            "  Wait: wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled\n"
            "  Gesture: swipe, scroll (text=direction)\n"
            "  Assert: assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute (meta.attribute + expect)\n"
            "  Keyboard: pressKey (text=key), keyboardAction (legacy alias), hideKeyboard\n"
            "  App: launchApp, closeApp, resetApp (text=bundleId/package, optional)\n"
            "  Capture: takeScreenshot, getPageSource\n\n"
            + rules_block_ng + "\n\n"
            "IMPORTANT: For keyboard keys (return, done, go, next, search), use pressKey or keyboardAction instead of tap.\n"
            "Use hideKeyboard when you need to dismiss the keyboard without pressing a specific key.\n"
            "Keep selectors realistic for Appium. Use accessibilityId where possible.\n"
            "No markdown, no explanation, only JSON."
        )
        var_hint = _variable_hint(payload.project_id)
        figma_hint = _figma_hint()
        user_msg = f"Platform: {payload.platform}\nGoal:\n{payload.prompt}{var_hint}{figma_hint}"
        if live_xml_str:
            user_msg += f"\n\nCurrent page source (filtered):\n{live_xml_str}"

    body = _build_gemini_body(system_prompt, user_msg, screen_images, temperature=0.1)

    screens_used = len(screen_images) if grounded else 0
    try:
        parsed = await _gemini_call(api_key, model, body)
        steps = parsed.get("steps")
        if not isinstance(steps, list):
            raise HTTPException(status_code=502, detail="AI did not return steps[]")

        test_data = parsed.get("test_data") or {}
        steps, test_data = enforce_data_layer(steps, test_data)
        if raw_xmls:
            steps = sanitize_selector_packages(steps, raw_xmls)

        grounding_score = None
        if raw_xmls:
            steps, matched, total = validate_selectors_against_xml(steps, raw_xmls, payload.platform)
            grounding_score = {"matched": matched, "total": total}

            if grounded and total > 0 and matched / total < 0.8:
                ungrounded = [
                    f"Step {i}: {s.get('selector', {}).get('value', '?')}"
                    for i, s in enumerate(steps) if not s.get("_grounded", True)
                ]
                correction_msg = (
                    "You generated these steps but some selectors do NOT exist in the XML:\n"
                    + "\n".join(ungrounded[:10])
                    + "\n\nHere is the XML again. Fix ONLY the invalid selectors using attributes from the XML. "
                    "Return the complete step list.\n\nDOM CONTEXT\n==========\n" + xml_context
                )
                correction_body = _build_gemini_body(system_prompt, correction_msg, screen_images, temperature=0.05)
                try:
                    parsed2 = await _gemini_call(api_key, model, correction_body)
                    steps2 = parsed2.get("steps")
                    if isinstance(steps2, list) and len(steps2) > 0:
                        td2 = parsed2.get("test_data") or {}
                        steps2, td2 = enforce_data_layer(steps2, td2)
                        steps2 = sanitize_selector_packages(steps2, raw_xmls)
                        test_data.update(td2)
                        steps2, m2, t2 = validate_selectors_against_xml(steps2, raw_xmls, payload.platform)
                        if t2 == 0 or m2 / t2 >= matched / max(total, 1):
                            steps = steps2
                            grounding_score = {"matched": m2, "total": t2}
                except Exception:
                    pass

        data_set_id: int | None = None
        if test_data and payload.project_id:
            data_set_id = _auto_create_data_layer(
                payload.project_id,
                payload.prompt[:60].strip() or "AI Generated",
                test_data,
            )

        return {
            "steps": steps, "grounded": grounded, "screens_used": screens_used,
            "data_set_id": data_set_id, "test_data": test_data,
            "grounding_score": grounding_score,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI generate steps failed: {e}")


# ── Data Layer Auto-Creation ───────────────────────────────────────────


def _auto_create_data_layer(
    project_id: int,
    context_name: str,
    test_data: dict[str, str],
) -> int | None:
    """Auto-create a DataFolder + DataSet from AI-extracted test_data.

    ``context_name`` is typically the suite or test name, used to name the folder/set.
    Returns the new DataSet id, or None if no data was extracted.
    """
    if not test_data:
        return None

    from ..models import DataFolder, DataSet

    with SessionLocal() as db:
        folder = (
            db.query(DataFolder)
            .filter(DataFolder.project_id == project_id, DataFolder.name == context_name)
            .first()
        )
        if not folder:
            folder = DataFolder(
                project_id=project_id,
                name=context_name,
                description=f"Auto-generated for '{context_name}'",
            )
            db.add(folder)
            db.commit()
            db.refresh(folder)

        ds_name = f"{context_name} Data"
        existing = (
            db.query(DataSet)
            .filter(DataSet.project_id == project_id, DataSet.folder_id == folder.id, DataSet.name == ds_name)
            .first()
        )
        if existing:
            existing.variables = {**(existing.variables or {}), **test_data}
            db.commit()
            return existing.id

        ds = DataSet(
            project_id=project_id,
            folder_id=folder.id,
            name=ds_name,
            variables=test_data,
        )
        db.add(ds)
        db.commit()
        db.refresh(ds)
        return ds.id


# ── AI Generate Test Suite (bulk) ─────────────────────────────────────

class ManualTestCase(BaseModel):
    name: str
    steps: list[str]
    expected: Optional[str] = None
    priority: Optional[str] = None


def _format_manual_tests(tests: list[ManualTestCase]) -> str:
    lines: list[str] = []
    for i, t in enumerate(tests, 1):
        lines.append(f"TEST {i}: {t.name}")
        for j, step in enumerate(t.steps, 1):
            lines.append(f"  Step {j}: {step}")
        if t.expected:
            lines.append(f"  Expected Result: {t.expected}")
        if t.priority:
            lines.append(f"  Priority: {t.priority}")
        lines.append("")
    return "\n".join(lines)


class GenerateSuiteRequest(BaseModel):
    platform: str
    prompt: str
    page_source_xml: str = ""
    project_id: int
    suite_id: int
    folder_id: Optional[int] = None
    build_ids: Optional[list[int]] = None
    manual_tests: Optional[list[ManualTestCase]] = None
    confluence_page_id: Optional[str] = None
    use_figma: bool = False


@router.post("/api/ai/generate-suite")
async def generate_suite(payload: GenerateSuiteRequest) -> dict[str, Any]:
    if payload.platform not in ("android", "ios_sim"):
        raise HTTPException(status_code=400, detail="platform must be android|ios_sim")

    s = load_settings()
    api_key, model = ai_creds(s)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    # ── Confluence PRD fetch (if page_id provided) ──
    confluence_prd = ""
    confluence_title = ""
    if payload.confluence_page_id:
        try:
            from .integrations import _confluence_auth_headers, _html_to_text
            confluence_base = (s.get("confluence_url") or "").rstrip("/")
            conf_headers = _confluence_auth_headers(s)
            if confluence_base and conf_headers:
                async with httpx.AsyncClient(timeout=20) as _cc:
                    _cr = await _cc.get(
                        f"{confluence_base}/rest/api/content/{payload.confluence_page_id}",
                        params={"expand": "body.storage"},
                        headers=conf_headers,
                    )
                    if _cr.status_code == 200:
                        _cd = _cr.json()
                        _html = (_cd.get("body") or {}).get("storage", {}).get("value", "")
                        confluence_prd = _html_to_text(_html)
                        confluence_title = _cd.get("title", "")
        except Exception:
            pass

    # ── Figma context (if requested) ──
    figma_context = ""
    if payload.use_figma:
        try:
            figma_token = (s.get("figma_token") or "").strip()
            figma_file_key = (s.get("figma_file_key") or "").strip()
            if figma_token and figma_file_key:
                from .integrations import (
                    _cached_figma_component_names,
                    _cached_figma_file_overview,
                    _figma_components_ttl_bucket,
                )
                _fb = _figma_components_ttl_bucket()
                _names = list(_cached_figma_component_names(figma_token, figma_file_key, _fb))
                _overview = _cached_figma_file_overview(figma_token, figma_file_key, _fb)
                parts: list[str] = [f"Figma File: {_overview.get('name', '')}"]
                for pg in _overview.get("pages", []):
                    parts.append(f"\nPage: {pg['name']}")
                    for fr in pg.get("frames", []):
                        parts.append(f"  {fr['type']}: {fr['name']}")
                if _names:
                    parts.append(f"\nComponents ({len(_names)}): {', '.join(_names[:50])}")
                figma_context = "\n".join(parts)
        except Exception:
            pass

    # Build the combined prompt with all sources
    effective_prompt = payload.prompt
    if confluence_prd:
        effective_prompt = (
            f"PRD SOURCE (from Confluence page: {confluence_title}):\n"
            f"{'='*60}\n{confluence_prd[:12000]}\n{'='*60}\n\n"
            f"Additional instructions: {payload.prompt}" if payload.prompt.strip() else
            f"PRD SOURCE (from Confluence page: {confluence_title}):\n"
            f"{'='*60}\n{confluence_prd[:12000]}\n{'='*60}\n\n"
            "Generate comprehensive test cases covering all functional requirements described in this PRD."
        )

    xml_context, screen_images, screens_for_prompt, raw_xmls = _load_screen_context(
        payload.project_id, payload.folder_id, [], payload.build_ids, None,
        payload.platform, effective_prompt,
        max_screens=10,
        max_elements_per_screen=40,
    )
    grounded = bool(xml_context)
    using_choices_suite = (
        "accessibilityId|id|xpath|-android uiautomator"
        if payload.platform == "android"
        else "accessibilityId|id|xpath|-ios predicate string|-ios class chain"
    )
    if grounded:
        android_rules = android_selector_generation_rules(screens_for_prompt) if payload.platform == "android" else ""
        ios_rules = ios_selector_generation_rules(screens_for_prompt) if payload.platform == "ios_sim" else ""

        _suite_xml = "".join(getattr(s, "xml_snapshot", "") or "" for s in screens_for_prompt[:3])
        if payload.platform == "android" and _suite_xml and is_compose_screen(_suite_xml):
            _suite_screen_type = "compose"
        elif payload.platform == "ios_sim" and _suite_xml and is_swiftui_screen(_suite_xml):
            _suite_screen_type = "swiftui"
        else:
            _suite_screen_type = "native"
        suite_rules_block = build_rules_block(payload.prompt, xml_context, payload.platform, _suite_screen_type)

        # Build a screen inventory so AI knows all available screens and their order
        screen_names_list = [getattr(s, "name", "") for s in screens_for_prompt if getattr(s, "name", "")]
        screen_inventory = ", ".join(screen_names_list) if screen_names_list else "(see XML below)"
        n_screens = len(screen_names_list)

        sel_tc = '{"using":"' + using_choices_suite + '","value":"..."}'
        system_prompt = (
            "You are a senior mobile QA automation engineer who writes THOROUGH, END-TO-END test cases.\n\n"
            "## OUTPUT FORMAT\n"
            "Return ONLY valid JSON with this shape:\n"
            '{"test_cases": [{"name": "...", "acceptance_criteria": "...", "steps": [{"type": "<step_type>",'
            ' "selector": ' + sel_tc + ', "text": "...", "ms": 1000, "expect":"...", "meta": {...}}]}, ...],'
            ' "test_data": {"variable_name": "value", ...}}\n\n'
            "## CRITICAL: END-TO-END FLOW TESTING (READ CAREFULLY)\n"
            "Each test case MUST be a COMPLETE user journey that walks through MULTIPLE SCREENS in sequence.\n"
            "You have access to " + str(n_screens) + " screens: [" + screen_inventory + "]\n"
            "RULES FOR FLOW-BASED TESTS:\n"
            "1. Every test case MUST start from the entry screen and navigate through screens in logical order.\n"
            "2. Every test case should have AT LEAST 15-30 steps. A test with fewer than 10 steps is TOO SHORT.\n"
            "3. When a screen has input fields (EditText, TextInputLayout), the test MUST fill them with test data.\n"
            "4. When a screen has buttons/CTAs, the test MUST tap them to navigate to the next screen.\n"
            "5. After each navigation action (tap a button, submit a form), add waitForVisible on an element "
            "from the NEXT screen's XML to verify the transition happened.\n"
            "6. After data entry on a screen, always hide the keyboard before tapping navigation buttons.\n"
            "7. DO NOT create single-screen tests that just tap one element. Every test must cross multiple screens.\n"
            "8. The test flow should mirror real user behavior: open app -> see screen -> interact -> navigate -> verify -> continue.\n\n"
            "## SCREEN FLOW PATTERN (follow this for EVERY test)\n"
            "For each screen the user visits in order:\n"
            "  a. waitForVisible — verify you're on the right screen (use a unique element from that screen's XML)\n"
            "  b. Interact — fill all input fields, make selections, toggle options\n"
            "  c. Assert — verify current screen state if needed\n"
            "  d. Navigate — tap the primary button/CTA to move to the next screen\n"
            "  e. Repeat a-d for the next screen\n\n"
            "## VERIFICATION RULES (CRITICAL — applies to ALL modules)\n"
            "You MUST use assertText / assertTextContains / assertVisible to verify outcomes, not just navigate.\n"
            "1. AFTER FORM SUBMISSION: When data is entered on Screen A and submitted to Screen B (confirmation/review), "
            "use assertText on Screen B to verify EVERY piece of data that was entered on Screen A "
            "(amounts, names, selections, counts). Do NOT just waitForVisible on Screen B.\n"
            "2. AFTER CALCULATIONS: If the app computes values (split amounts, totals, taxes, discounts), "
            "use assertText to verify the computed values are correct.\n"
            "3. AFTER ITEM CREATION: If a flow creates an item (payment, order, message, request), "
            "navigate to the list/inbox screen and assertVisible on the newly created item. "
            "Verify the item shows correct status (e.g., Pending, Sent, Active).\n"
            "4. SUCCESS SCREENS: On success/confirmation screens, assertVisible on the success indicator AND "
            "assertText/assertVisible on each listed item or recipient.\n"
            "5. ERROR STATES: When testing error scenarios, assertVisible on the error message/indicator AND "
            "assertVisible that you are STILL on the same screen (did not navigate forward).\n"
            "6. TOGGLE/CHECKBOX VERIFICATION: After tapping a toggle or checkbox, use assertChecked or assertAttribute "
            "to verify the state changed.\n\n"
            "## SELECTOR QUALITY (applies to ALL modules)\n"
            "1. ALWAYS prefer resource-id selectors over class-based selectors.\n"
            "2. NEVER use className('android.widget.EditText').instance(N) when a resource-id exists for that field. "
            "Check the XML — if the EditText or its parent has a resource-id, use it.\n"
            "3. For amount/text verification, use xpath only when asserting specific values within complex layouts. "
            "Build xpath using resource-ids from the XML, not class names.\n"
            "4. For list items, prefer resource-id with index (e.g., list_item_0, list_item_1) over generic xpath.\n\n"
            "## MANDATORY TEST TYPES (for EVERY module, not just this one)\n"
            "Your test suite MUST include at minimum:\n"
            "- 1-2 HAPPY PATH tests: Complete end-to-end flows through the main feature\n"
            "- 1 ERROR/NEGATIVE test: Invalid inputs, validation failures, mismatched data — verify error state and no forward navigation\n"
            "- 1 NAVIGATION test: Back button at key screens, or cancel/abort mid-flow — verify correct screen after back\n"
            "- 1 EDGE CASE test: Boundary values, single vs multiple items, empty states, minimum/maximum inputs\n"
            "If the module has list/inbox views, add a test that verifies items appear correctly after creation.\n\n"
            "## TEST DATA\n"
            "Extract ALL test data (emails, phones, passwords, OTPs, URLs, names, amounts, etc.) into the test_data object.\n"
            "Use ${variable_name} syntax in step text/expect fields instead of hardcoded values.\n\n"
            "## AVAILABLE STEP TYPES\n"
            "tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
            "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
            "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
            "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n\n"
            + suite_rules_block + "\n\n"
            + android_rules
            + ios_rules
            + "## SELECTOR RULES\n"
            "PRIORITY ORDER (use first match; follow per-screen Compose rules above when applicable):\n"
            "1. resource-id (most stable)\n"
            "2. content-desc / accessibility id (stable)\n"
            "3. text (fragile — only if no ID available)\n"
            "4. xpath (last resort — only if nothing else exists)\n"
            "IMPORTANT: You MUST use only selectors that exist in the provided XML. Never invent selectors.\n"
            "Every selector must be found verbatim in the DOM CONTEXT below.\n\n"
            "## ACCEPTANCE CRITERIA\n"
            "For each test case, include acceptance_criteria that is SPECIFIC and VERIFIABLE:\n"
            "- Name the EXACT screen the test should be on at each major step (reference resource-ids or screen names from XML)\n"
            "- Name the EXACT elements that must be visible to confirm correct navigation\n"
            "- List EVERY screen the test visits in order\n"
            "- State the expected outcome with concrete, measurable conditions\n"
            "BAD: 'User can split money'\n"
            "GOOD: 'Start on Home screen (home_container visible). Tap Split Money. On Split screen "
            "(split_title visible), enter amount ${amount} in amount_field, select contact ${contact_name}, "
            "tap Continue. On Confirm screen (confirm_summary visible), verify amount matches, tap Send. "
            "On Success screen (success_icon visible), verify confirmation message.'\n\n"
            "For keyboard keys (return, done, go), use pressKey or keyboardAction. Use hideKeyboard when needed.\n"
            "No markdown, no explanation, only JSON."
        )
        if payload.manual_tests:
            system_prompt += (
                "\n\nSTRICT RULES — CSV Translation Mode:\n"
                "- Preserve the exact test name from each manual test case. Do NOT rename.\n"
                "- Generate exactly one test_case per manual test provided. Do NOT invent new tests or merge existing ones.\n"
                "- Each manual step should expand into multiple Appium steps (e.g. 'Log in' = waitForVisible + tap + type + tap).\n"
                "- Every Expected Result must become an assertText or assertVisible step at the end of that test case.\n"
                "- Use real selectors from the DOM CONTEXT. Do not invent resource-ids.\n"
                "- Do NOT skip manual steps — every step must produce at least one Appium step.\n"
            )
        else:
            if confluence_prd:
                system_prompt += (
                    "\n\n## CONFLUENCE PRD MODE\n"
                    "A PRD document from Confluence is provided. You MUST:\n"
                    "1. Read the PRD carefully and identify ALL user flows described.\n"
                    "2. For EACH flow in the PRD, generate a test case that walks through the ENTIRE flow end-to-end.\n"
                    "3. Map each PRD requirement to specific screens from the Screen Library XML.\n"
                    "4. Each test should have 15-40 steps covering the full flow described in the PRD.\n"
                    "5. Include: Happy path for each flow, edge cases, error/negative scenarios, validation tests.\n"
                    "6. Generate 5-15 test cases to fully cover the PRD requirements.\n"
                    "7. DO NOT generate shallow 2-3 step tests. Each PRD requirement needs thorough multi-screen coverage.\n"
                )
            else:
                system_prompt += "\nGenerate 3-8 test cases covering happy path, edge cases, and error scenarios. Each test should be 15-30 steps.\n"
            if figma_context:
                system_prompt += (
                    "\n## FIGMA DESIGN CONTEXT RULES\n"
                    "Figma design context is provided showing the app's screen structure and components.\n"
                    "USE Figma context to:\n"
                    "1. Understand the SCREEN FLOW ORDER — Figma frames show the sequence of screens the user visits.\n"
                    "2. Identify ALL screens that exist in the app — every Figma frame represents a screen to test.\n"
                    "3. Understand what UI components exist on each screen (buttons, inputs, lists, etc.).\n"
                    "4. Map Figma component names to XML resource-ids to understand element purpose.\n"
                    "DO NOT:\n"
                    "- Use Figma component names as selectors — ONLY use XML resource-ids/content-desc.\n"
                    "- Skip screens shown in Figma. If Figma shows 6 screens, your tests should collectively cover all 6.\n"
                )

        var_hint_suite = _variable_hint(payload.project_id)
        figma_hint_suite = _figma_hint() if not figma_context else ""

        # Build screen flow summary for user_msg
        screen_flow_summary = ""
        if screen_names_list:
            screen_flow_summary = "\n\nAVAILABLE SCREENS IN ORDER (visit these in your test flows):\n"
            for i, sn in enumerate(screen_names_list, 1):
                screen_flow_summary += f"  {i}. {sn}\n"
            screen_flow_summary += (
                "Each test case should start from an early screen and walk forward through this sequence.\n"
                "Your tests COLLECTIVELY must cover ALL screens listed above.\n"
            )

        user_msg = f"Platform: {payload.platform}\n\nDescribe the feature/suite to test:\n{effective_prompt}{var_hint_suite}{figma_hint_suite}"
        user_msg += screen_flow_summary
        if figma_context:
            user_msg += (
                f"\n\nFIGMA DESIGN CONTEXT\n====================\n{figma_context}\n\n"
                "IMPORTANT: Figma frames show the screens in flow order. Each frame is a screen the user visits. "
                "Your tests must follow this screen sequence. Use Figma to understand WHAT the user does on each screen, "
                "then use XML resource-ids/content-desc for actual selectors.\n"
            )
        user_msg += f"\n\nDOM CONTEXT (XML for each screen — use ONLY these selectors)\n{'='*50}\n{xml_context}"
        if payload.manual_tests:
            user_msg += f"\n\nMANUAL TEST CASES TO TRANSLATE:\n{_format_manual_tests(payload.manual_tests)}"
    else:
        _suite_live = (payload.page_source_xml or "").strip()
        if payload.platform == "android" and _suite_live and is_compose_screen(payload.page_source_xml):
            _suite_st_ng = "compose"
        elif payload.platform == "ios_sim" and _suite_live and is_swiftui_screen(payload.page_source_xml):
            _suite_st_ng = "swiftui"
        else:
            _suite_st_ng = "native"

        suite_live_xml_str = ""
        if _suite_live:
            suite_live_xml_str = preprocess_live_xml(payload.page_source_xml, payload.platform, description=payload.prompt)

        suite_rules_ng = build_rules_block(payload.prompt, suite_live_xml_str, payload.platform, _suite_st_ng)

        sel_tc_ng = '{"using":"' + using_choices_suite + '","value":"..."}'
        system_prompt = (
            "You are a senior mobile QA automation engineer who writes THOROUGH, END-TO-END test cases.\n"
            "Generate MULTIPLE test cases for a test suite. Each test case should be a COMPLETE user journey "
            "with AT LEAST 15-30 steps.\n"
            "Return ONLY valid JSON with this shape:\n"
            '{"test_cases": [{"name": "...", "acceptance_criteria": "...", "steps": [{"type": "<step_type>",'
            ' "selector": ' + sel_tc_ng + ', "text": "...", "ms": 1000, "expect":"...", "meta": {...}}]}, ...],'
            ' "test_data": {"variable_name": "value", ...}}\n'
            "IMPORTANT: Extract ALL test data into the test_data object. Use ${variable_name} in step text/expect fields.\n"
            "Each test must follow end-to-end screen flows: navigate, interact, verify at every screen.\n\n"
            "VERIFICATION RULES:\n"
            "- After form submission to a confirmation screen, use assertText to verify entered data carries over.\n"
            "- After calculations (totals, splits), assertText on computed values.\n"
            "- After item creation, navigate to list view and assertVisible on the new item.\n"
            "- On error scenarios, assertVisible on error message AND assert you're still on the same screen.\n\n"
            "MANDATORY TEST TYPES:\n"
            "- 1-2 happy path tests, 1 error/negative test, 1 navigation/back test, 1 edge case test.\n\n"
            "SELECTOR QUALITY:\n"
            "- Always prefer resource-id over class+instance selectors.\n\n"
            "Available step types: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
            "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
            "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
            "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n\n"
            + suite_rules_ng + "\n\n"
            "For each test case, include acceptance_criteria that is SPECIFIC and VERIFIABLE:\n"
            "- Name the EXACT screen the test should be on at each major step\n"
            "- Name the EXACT elements that must be visible to confirm correct navigation\n"
            "- State the expected outcome with concrete, measurable conditions\n"
            "For keyboard keys (return, done, go), use pressKey or keyboardAction. Use hideKeyboard when needed.\n"
            "No markdown, no explanation, only JSON."
        )
        if payload.manual_tests:
            system_prompt += (
                "\n\nSTRICT RULES — CSV Translation Mode:\n"
                "- Preserve the exact test name from each manual test case. Do NOT rename.\n"
                "- Generate exactly one test_case per manual test provided. Do NOT invent new tests or merge existing ones.\n"
                "- Each manual step should expand into multiple Appium steps.\n"
                "- Every Expected Result must become an assertText or assertVisible step at the end of that test case.\n"
            )
        else:
            if confluence_prd:
                system_prompt += (
                    "\n\nCONFLUENCE PRD MODE: Generate COMPREHENSIVE end-to-end test cases covering "
                    "ALL functional requirements in the PRD. Each test must be 15+ steps. Generate 5-15 test cases.\n"
                )
            else:
                system_prompt += "\nGenerate 3-8 test cases covering happy path, edge cases, and error scenarios. Each test should be 15-30 steps.\n"
            if figma_context:
                system_prompt += (
                    "\nFigma design context is provided. Use frame names as screen flow order. "
                    "Your tests must visit screens in the order shown by Figma frames. "
                    "Do NOT use Figma names as selectors.\n"
                )

        var_hint_suite_ng = _variable_hint(payload.project_id)
        figma_hint_ng = _figma_hint() if not figma_context else ""
        user_msg = f"Platform: {payload.platform}\n\nDescribe the feature/suite to test:\n{effective_prompt}{var_hint_suite_ng}{figma_hint_ng}"
        if figma_context:
            user_msg += f"\n\nFIGMA DESIGN CONTEXT\n====================\n{figma_context}\n\nUse Figma frame names to understand the screen flow and component names for meaningful test descriptions.\n"
        if suite_live_xml_str:
            user_msg += f"\n\nCurrent page source (filtered):\n{suite_live_xml_str}"
        if payload.manual_tests:
            user_msg += f"\n\nMANUAL TEST CASES TO TRANSLATE:\n{_format_manual_tests(payload.manual_tests)}"

    body = _build_gemini_body(system_prompt, user_msg, screen_images, temperature=0.1 if grounded else 0.15)

    # Retry once on transient failures (timeout, rate-limit, malformed response)
    last_error: Exception | None = None
    parsed: dict[str, Any] | None = None
    for attempt in range(2):
        try:
            parsed = await _gemini_call(api_key, model, body, timeout=120)
            break
        except HTTPException:
            raise
        except Exception as e:
            last_error = e
            logger.warning("generate-suite attempt %d failed: %s", attempt + 1, e)
            if attempt == 0:
                import asyncio
                await asyncio.sleep(2)
    if parsed is None:
        logger.exception("generate-suite failed after retries: %s", last_error)
        raise HTTPException(status_code=502, detail=f"AI generate suite failed after retry: {last_error}")

    try:
        raw_cases = parsed.get("test_cases")
        if not isinstance(raw_cases, list):
            raise HTTPException(status_code=502, detail="AI did not return test_cases[]")

        suite_test_data = parsed.get("test_data") or {}
        for tc in raw_cases:
            tc_steps = tc.get("steps")
            if isinstance(tc_steps, list):
                cleaned, extra = enforce_data_layer(tc_steps, suite_test_data)
                tc["steps"] = sanitize_selector_packages(cleaned, raw_xmls)
                suite_test_data.update(extra)

        # Post-generation quality scan
        quality_warnings: list[str] = []
        for tc in raw_cases:
            tc_steps = tc.get("steps")
            if not isinstance(tc_steps, list):
                continue
            tc_name = tc.get("name", "?")
            n_steps = len(tc_steps)
            if n_steps < 10:
                quality_warnings.append(f"'{tc_name}' has only {n_steps} steps (expected 15+)")
            step_types = [s.get("type", "") for s in tc_steps]
            has_assert_text = any(t in ("assertText", "assertTextContains") for t in step_types)
            has_any_assert = any(t.startswith("assert") for t in step_types)
            if not has_assert_text and n_steps >= 10:
                quality_warnings.append(f"'{tc_name}' has no assertText/assertTextContains — missing data verification")
            if not has_any_assert:
                quality_warnings.append(f"'{tc_name}' has zero assertion steps")
        if quality_warnings:
            logger.info("Suite generation quality warnings: %s", "; ".join(quality_warnings))

        created: list[dict] = []
        with SessionLocal() as db:
            p = db.query(Project).filter(Project.id == payload.project_id).first()
            if not p:
                raise HTTPException(status_code=404, detail="Project not found")
            suite_obj = db.query(TestSuite).filter(TestSuite.id == payload.suite_id).first()
            if not suite_obj:
                raise HTTPException(status_code=404, detail="Suite not found")
            for tc in raw_cases:
                name = tc.get("name") or f"Generated {len(created) + 1}"
                steps = tc.get("steps")
                if not isinstance(steps, list):
                    continue
                ac = tc.get("acceptance_criteria") or payload.prompt[:2000]
                plat = payload.platform if payload.platform in ("android", "ios_sim") else "android"
                ps = {"android": [], "ios_sim": []}
                ps[plat] = list(steps)
                legacy_android = list(ps.get("android") or [])
                t = TestDefinition(
                    project_id=payload.project_id,
                    suite_id=payload.suite_id,
                    name=name,
                    steps=legacy_android,
                    platform_steps=ps,
                    acceptance_criteria=ac,
                )
                db.add(t)
                db.commit()
                db.refresh(t)
                created.append({"id": t.id, "name": t.name, "steps_count": len(steps)})

        suite_data_set_id: int | None = None
        if suite_test_data:
            suite_data_set_id = _auto_create_data_layer(
                payload.project_id,
                suite_obj.name if suite_obj else "AI Suite",
                suite_test_data,
            )

        return {
            "created": len(created),
            "test_cases": created,
            "data_set_id": suite_data_set_id,
            "quality_warnings": quality_warnings if quality_warnings else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("generate-suite post-processing failed: %s", e)
        raise HTTPException(status_code=502, detail=f"AI generate suite failed: {e}")


# ── AI Fix Failed Steps ───────────────────────────────────────────────


def _build_tap_diagnosis_for_ai(
    failed_step: dict[str, Any],
    failed_step_index: int,
    all_steps: list[dict[str, Any]],
    step_results: list[dict[str, Any]],
    page_source_xml: str,
    page_source_xml_raw: str = "",
    target_platform: str = "android",
) -> tuple[dict[str, Any] | None, str]:
    tap_diagnosis: dict[str, Any] | None = None
    tap_diagnosis_text = ""
    _DIAGNOSABLE_TYPES = {
        "tap", "doubleTap", "longPress", "type", "clear", "clearAndType",
        "waitForVisible", "waitForNotVisible", "waitForEnabled", "waitForDisabled",
        "assertText", "assertTextContains", "assertVisible", "assertNotVisible",
        "assertEnabled", "assertChecked", "assertAttribute", "scroll",
    }
    if failed_step.get("type") not in _DIAGNOSABLE_TYPES:
        return tap_diagnosis, tap_diagnosis_text
    sel = failed_step.get("selector") or {}
    strategy = sel.get("using", "accessibilityId")
    value = (sel.get("value") or "").strip()
    diag_xml = (page_source_xml_raw or page_source_xml or "").strip()
    if not value or not diag_xml:
        return tap_diagnosis, tap_diagnosis_text

    diag = diagnose_tap_failure(
        strategy=strategy,
        value=value,
        page_source_xml=diag_xml,
        step_index=failed_step_index,
        all_steps=all_steps,
        step_results=step_results,
        platform=target_platform,
    )

    tap_diagnosis = {
        "found": diag.found,
        "root_cause": diag.root_cause,
        "root_cause_detail": diag.root_cause_detail,
        "is_clickable": diag.is_clickable,
        "is_visible": diag.is_visible,
        "recommended_wait_ms": diag.recommended_wait_ms,
        "suggestions": [
            {"strategy": s.strategy, "value": s.value, "score": s.score, "label": s.label}
            for s in diag.suggestions
        ],
    }

    rc_map = {
        "wrong_selector": "WRONG SELECTOR — the element exists but the selector strategy is wrong",
        "timing_race": "TIMING — the element exists but was not yet rendered when Appium looked for it",
        "scrolled_off": "SCROLLED — element is in the page source but outside the visible viewport",
        "overlay_blocking": "OVERLAY — a dialog or loading screen is blocking the element",
        "element_disabled": "DISABLED — element found but enabled=false",
        "wrong_screen": "WRONG SCREEN — the app navigated to an unexpected page",
        "element_missing": "MISSING — element not present in the page source at all",
        "xml_parse_failed": "XML PARSE — hierarchy text was not valid strict XML for the rule-based debugger",
    }
    tap_diagnosis_text = (
        f"\n=== TAP DEBUGGER DIAGNOSIS (run BEFORE this prompt) ===\n"
        f"Root cause: {rc_map.get(diag.root_cause, diag.root_cause)}\n"
        f"Detail: {diag.root_cause_detail}\n"
        f"Element found in XML: {diag.found}\n"
    )
    if diag.suggestions:
        tap_diagnosis_text += "Working selectors ranked by reliability:\n"
        for sug in diag.suggestions[:3]:
            tap_diagnosis_text += f"  - {sug.strategy}='{sug.value}' ({sug.score}% reliable)\n"
    if diag.recommended_wait_ms:
        tap_diagnosis_text += f"Recommended: add waitForVisible {diag.recommended_wait_ms}ms before tap\n"
    tap_diagnosis_text += (
        "\nUSE THE DIAGNOSIS ABOVE. Do not guess selectors — pick from the working list when suggestions exist.\n"
        "If root_cause is timing, insert a waitForVisible step before the tap.\n"
        "If root_cause is wrong_selector, replace the selector with the highest-scored suggestion.\n"
        "If root_cause is scrolled, insert a swipe step before the tap.\n"
        "=== END DIAGNOSIS ===\n"
    )

    return tap_diagnosis, tap_diagnosis_text


class FixStepsRequest(BaseModel):
    platform: str
    original_steps: list[dict[str, Any]]
    step_results: list[dict[str, Any]]
    failed_step_index: int
    error_message: str = ""
    page_source_xml: str = ""
    page_source_xml_raw: str = ""
    test_name: str = ""
    screenshot_base64: str = ""
    already_tried_fixes: list[dict[str, Any]] = []
    acceptance_criteria: str = ""
    app_context: str = ""
    target_platform: str = "android"
    # Data layer context for variable-aware fixing
    data_context: dict[str, str] = {}
    data_set_id: Optional[int] = None
    template_steps: list[dict[str, Any]] = []


@router.post("/api/ai/fix-steps")
async def fix_steps(request: Request, payload: FixStepsRequest) -> dict[str, Any]:
    _check_rate_limit(request)
    s = load_settings()
    api_key, model = ai_creds(s)

    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    failed_step = payload.original_steps[payload.failed_step_index] if payload.failed_step_index < len(payload.original_steps) else {}

    tap_diagnosis, tap_diagnosis_text = _build_tap_diagnosis_for_ai(
        failed_step,
        payload.failed_step_index,
        payload.original_steps,
        payload.step_results,
        payload.page_source_xml,
        payload.page_source_xml_raw,
        payload.target_platform,
    )

    android_pkg = parse_android_package(payload.app_context or "")
    failure_diagnosis = classify_failure_for_ai_fix(
        failed_step,
        payload.error_message,
        payload.page_source_xml_raw,
        payload.page_source_xml,
        payload.target_platform,
        android_pkg,
        tap_diagnosis,
        step_results=payload.step_results,
    )

    passed_steps = []
    for i, r in enumerate(payload.step_results):
        if r.get("status") == "passed" and i < len(payload.original_steps):
            passed_steps.append({"index": i, "step": payload.original_steps[i]})

    # Build contextual rules for fix prompt using the available XML
    _fix_xml = payload.page_source_xml_raw or payload.page_source_xml or ""
    _fix_desc = payload.error_message + " " + payload.test_name
    if payload.target_platform == "android" and _fix_xml and is_compose_screen(_fix_xml):
        _fix_screen_type = "compose"
    elif payload.target_platform in ("ios", "ios_sim") and _fix_xml and is_swiftui_screen(_fix_xml):
        _fix_screen_type = "swiftui"
    else:
        _fix_screen_type = "native"
    fix_rules_block = build_rules_block(_fix_desc, _fix_xml, payload.target_platform, _fix_screen_type)

    system_prompt = (
        "You are a senior mobile QA automation engineer debugging a failed Appium test.\n"
        "A test run failed at a specific step. You have:\n"
        "- The full list of original test steps\n"
        "- Which steps passed and which failed\n"
        "- The error message from the failure\n"
        "- The actual page source XML showing what elements are on screen right now\n"
        "- A SCREENSHOT of the device screen at the moment of failure (if provided as an image)\n\n"
        "Your job:\n"
        "1. Look at the screenshot to understand what the user actually sees on screen\n"
        "2. Analyze WHY the step failed (wrong selector? element not present? wrong screen?)\n"
        "3. Cross-reference the screenshot with the page source XML to find the CORRECT selector\n"
        "4. Return FIXED steps that will work based on the actual screen state\n"
        "5. KEEP ALL PASSED STEPS EXACTLY AS-IS — do not change their selectors, types, order, or values.\n"
        "6. ONLY modify the FAILED step and at most 1-2 steps immediately after it if they depend on the fix.\n"
        "7. NEVER add new assertion steps (assertChecked, assertText, assertVisible) that were NOT in the original test.\n"
        "8. NEVER expand the test with additional steps beyond what was originally there. The step count should stay approximately the same.\n"
        "9. If you need to add a waitForVisible before a fixed step, that is OK. But do NOT add new verification steps.\n\n"
        + fix_rules_block + "\n\n"
        "SOURCE OF TRUTH (when provided): If acceptance_criteria is given, your fix MUST preserve the intended behavior. "
        "Do NOT change what the test validates. If the failure is because the app is on the WRONG screen (e.g., Sign Up instead of Login), "
        "do NOT change the test to validate the wrong screen. The fix should either navigate back to the correct flow, or add an assertion that fails when on the wrong screen. "
        "Never invert or alter the expected outcome.\n\n"
        "UNFAMILIAR = FAIL: If the screen or flow does not match the test's intended purpose (from acceptance_criteria), the test SHOULD fail and report. "
        "Do not make the test pass by validating whatever is on screen. Stay aligned with the test case motive.\n\n"
        "CRITICAL RULES FOR KEYBOARD / SYSTEM UI ELEMENTS:\n"
        "- Keyboard buttons (return, done, go, next, search, send) are NOT normal UI elements.\n"
        "- NEVER use 'tap' on keyboard keys. They belong to iOS/Android system UI.\n"
        "- Instead, use one of these step types:\n"
        '  * {"type": "keyboardAction", "text": "return"} — presses the key via mobile: pressButton\n'
        '  * {"type": "hideKeyboard"} — dismisses the keyboard entirely\n'
        '  * Tap on the next field or a non-keyboard button to move focus\n'
        "- If the failed step was tapping 'return', 'done', 'go' etc., ALWAYS replace with keyboardAction.\n\n"
        "Available step types:\n"
        "  Tapping: tap, doubleTap, longPress, tapByCoordinates (meta.x, meta.y)\n"
        "  Text: type, clear, clearAndType\n"
        "  Wait: wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled\n"
        "  Gesture: swipe, scroll (text=direction)\n"
        "  Assert: assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute (meta.attribute + expect)\n"
        "  Keyboard: pressKey (text=key: return|done|go|next|search|send|back|home|enter|delete), keyboardAction (legacy alias), hideKeyboard (no selector needed)\n"
        "  App: launchApp, closeApp, resetApp (text=bundleId/package, optional)\n"
        "  Capture: takeScreenshot, getPageSource\n\n"
        "Return ONLY valid JSON with this shape:\n"
        '{"analysis": "Brief explanation of what went wrong and how you fixed it",'
        ' "fix_type": "step|data|both|bug",'
        ' "fixed_steps": [{"type": "<step_type>",'
        ' "selector": {"using":"accessibilityId|id|xpath","value":"..."},'
        ' "text": "...", "ms": 1000, "expect":"...", "meta": {...}}],'
        ' "bug_report": {"title": "...", "severity": "critical|major|minor", '
        '"expected_screen": "...", "actual_screen": "...", '
        '"expected_behavior": "...", "actual_behavior": "...", "evidence": "..."},'
        ' "data_fixes": {"variable_name": "new_value", ...},'
        ' "changes": [{"step_index": 0, "was": "...", "now": "...", "reason": "..."}]}\n\n'
        "fix_type:\n"
        "  'step' = fix selector/structure only\n"
        "  'data' = update variable values only\n"
        "  'both' = fix steps AND update data\n"
        "  'bug' = the app has a BUG — the test is correct but the app is wrong. Fill bug_report and return fixed_steps UNCHANGED.\n"
        "When fix_type='bug': the app is showing unexpected behavior that contradicts the acceptance_criteria. "
        "Do NOT rewrite the test to match broken app behavior. Report the bug instead.\n"
        "data_fixes: put ALL variable values here — both updated existing variables AND newly created ones. The backend will auto-apply them to the DataSet.\n\n"
        "CRITICAL DATA RULES (NEVER VIOLATE):\n"
        "1. NEVER hardcode raw test data (emails, phones, passwords, OTPs, URLs, names, amounts, codes, dates) in fixed_steps text/expect fields.\n"
        "2. If a fix introduces ANY new data value, CREATE a new variable. Add it to data_fixes and use ${new_variable_name} in the step.\n"
        "   - CRITICAL: The dictionary key in data_fixes MUST be the raw variable name ONLY. Do NOT wrap the key in ${}.\n"
        "   - CORRECT: \"data_fixes\": {\"amountToSplit\": 100}\n"
        "   - INCORRECT (WILL FAIL): \"data_fixes\": {\"${amountToSplit}\": 100}\n"
        "3. Existing ${variable_name} references must stay as ${variable_name}. If the value needs changing, update it ONLY in data_fixes (using the raw name as key).\n"
        "4. set fix_type to 'both' whenever data_fixes is non-empty.\n"
        "Example: if adding a step that types '12345' as OTP, create data_fixes: {\"otp_code\": \"12345\"} and use ${otp_code} in the step text.\n\n"
        "IMPORTANT: fixed_steps must be the COMPLETE list (all steps, not just changed ones).\n"
        "CRITICAL: The total step count of fixed_steps should be VERY CLOSE to the original step count. "
        "If the original had 25 steps, your fix should have 25-27 steps (at most 2 new waits). "
        "NEVER inflate a 25-step test to 50+ steps. Passed steps must be returned VERBATIM.\n"
        "Use accessibilityId or resource-id where possible, xpath as fallback.\n"
        "No markdown, no explanation outside the JSON."
        + AI_FIX_CLASSIFICATION_RULES
    )

    user_msg = (
        f"Platform: {payload.platform}\n"
        f"Target automation: {payload.target_platform} (use selectors appropriate for this stack: "
        f"Android UiAutomator2 vs iOS XCUITest)\n"
        f"Test: {payload.test_name}\n"
    )
    if payload.app_context:
        user_msg += f"App context: {payload.app_context}\n"

    if payload.template_steps and payload.data_context:
        user_msg += (
            f"\n=== TEMPLATE STEPS (with ${{var}} references — what is stored) ===\n"
            f"{json.dumps(payload.template_steps, indent=2)}\n\n"
            f"=== DATA CONTEXT (resolved variables from DataSet) ===\n"
            f"{json.dumps(payload.data_context, indent=2)}\n\n"
            f"=== RESOLVED STEPS (what actually ran) ===\n"
            f"{json.dumps(payload.original_steps, indent=2)}\n\n"
        )
    else:
        user_msg += (
            f"\n=== ORIGINAL STEPS ===\n{json.dumps(payload.original_steps, indent=2)}\n\n"
        )

    user_msg += "=== STEP RESULTS ===\n"
    for i, r in enumerate(payload.step_results):
        status = r.get("status", "pending")
        details = r.get("details", "")
        if isinstance(details, dict):
            details = details.get("error", str(details))
        user_msg += f"Step {i}: {status}"
        if details:
            user_msg += f" — {details}"
        user_msg += "\n"

    user_msg += (
        f"\n=== FAILED AT STEP {payload.failed_step_index} ===\n"
        f"Step definition: {json.dumps(failed_step, indent=2)}\n"
        f"Error: {payload.error_message}\n"
    )

    if tap_diagnosis_text:
        user_msg += tap_diagnosis_text

    user_msg += (
        "\n=== STRUCTURED FAILURE CLASSIFICATION ===\n"
        + build_failure_diagnosis_block(failure_diagnosis)
        + "\n"
    )

    if payload.acceptance_criteria:
        user_msg += f"\n=== SOURCE OF TRUTH / ACCEPTANCE CRITERIA (DO NOT VIOLATE) ===\n{payload.acceptance_criteria}\n\n"

    if payload.already_tried_fixes:
        user_msg += "\n=== ALREADY TRIED (do NOT repeat these approaches) ===\n"
        for i, prev in enumerate(payload.already_tried_fixes[:5], 1):
            analysis = prev.get("analysis", "")[:200]
            steps_preview = json.dumps(prev.get("fixed_steps", [])[:3])[:300]
            user_msg += f"Attempt {i}: {analysis}... | steps: {steps_preview}...\n"
        user_msg += "Try a DIFFERENT approach. Do not suggest the same or similar fix.\n\n"

        # Escalation: after 3+ failed attempts on InvalidElementStateException, force childSelector pattern
        _err_lower = (payload.error_message or "").lower()
        if len(payload.already_tried_fixes) >= 2 and (
            "cannot set the element" in _err_lower
            or "invalidelementstate" in _err_lower
        ):
            user_msg += (
                "=== ESCALATION: MANDATORY FIX PATTERN ===\n"
                "You have failed this fix multiple times. The error is InvalidElementStateException.\n"
                "This ALWAYS means you are targeting a WRAPPER element instead of the actual input.\n\n"
                "MANDATORY PATTERN (Android):\n"
                '1. tap the wrapper: {"type":"tap","selector":{"using":"-android uiautomator","value":"new UiSelector().resourceId(\\"<wrapper_id>\\")"}}\n'
                '2. type into child: {"type":"type","selector":{"using":"-android uiautomator","value":"new UiSelector().resourceId(\\"<wrapper_id>\\").childSelector(new UiSelector().className(\\"android.widget.EditText\\"))"},"text":"..."}\n'
                '3. hideKeyboard: {"type":"hideKeyboard"}\n\n'
                "MANDATORY PATTERN (iOS):\n"
                '1. tap the wrapper: {"type":"tap","selector":{"using":"-ios predicate string","value":"name == \'<wrapper_name>\'"}}\n'
                '2. type into child: {"type":"type","selector":{"using":"-ios class chain","value":"**/XCUIElementTypeTextField"},"text":"..."}\n'
                '3. hideKeyboard: {"type":"hideKeyboard"}\n\n'
                "Do NOT try adding waits, scrolls, or timeout bumps. The element IS present and visible — "
                "you are simply targeting the wrong node in the hierarchy.\n"
                "Look at the XML: find the resource-id from the failed selector, check its class — if it's NOT EditText/TextField, "
                "use .childSelector() to target the child EditText.\n"
                "=== END ESCALATION ===\n\n"
            )

    if payload.acceptance_criteria:
        screen_bug = check_screen_identity(payload.acceptance_criteria, payload.page_source_xml_raw or payload.page_source_xml or "")
        if screen_bug:
            user_msg += (
                "\n=== SCREEN IDENTITY CHECK (PRE-ANALYSIS) ===\n"
                + build_failure_diagnosis_block(screen_bug)
                + "\n"
            )

    if payload.page_source_xml:
        filtered_xml = preprocess_live_xml(
            payload.page_source_xml,
            payload.platform,
            description=f"{payload.test_name} {payload.error_message}",
        )
        user_msg += f"\n=== PAGE SOURCE (filtered) ===\n{filtered_xml}\n"

    fix_images: list[tuple[str, str]] = []
    if payload.screenshot_base64:
        raw_b64 = payload.screenshot_base64
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        fix_images.append(("failure_screenshot", raw_b64))

    body = _build_gemini_body(system_prompt, user_msg, fix_images, temperature=0.1)

    try:
        parsed = await _gemini_call(api_key, model, body)
        fixed = parsed.get("fixed_steps")
        if not isinstance(fixed, list):
            raise HTTPException(status_code=502, detail="AI did not return fixed_steps[]")

        fix_type = parsed.get("fix_type", "step")
        bug_report = parsed.get("bug_report") if fix_type == "bug" else None
        data_fixes = parsed.get("data_fixes") or {}
        if isinstance(data_fixes, dict):
            data_fixes = {k.replace("${", "").replace("}", ""): v for k, v in data_fixes.items()}

        if fix_type != "bug":
            fixed, extra = enforce_data_layer(fixed, data_fixes)
            data_fixes.update(extra)
            _fix_raw_xmls = [x for x in [payload.page_source_xml_raw, payload.page_source_xml] if x and x.strip()]
            if _fix_raw_xmls:
                fixed = sanitize_selector_packages(fixed, _fix_raw_xmls)

        data_set_updated = False
        if data_fixes and payload.data_set_id:
            from ..models import DataSet as DataSetModel
            with SessionLocal() as db:
                ds = db.query(DataSetModel).filter(DataSetModel.id == payload.data_set_id).first()
                if ds:
                    ds.variables = {**(ds.variables or {}), **data_fixes}
                    db.commit()
                    data_set_updated = True

        return {
            "analysis": parsed.get("analysis", ""),
            "fixed_steps": fixed,
            "changes": parsed.get("changes", []),
            "fix_type": fix_type,
            "bug_report": bug_report,
            "data_fixes": data_fixes,
            "data_set_updated": data_set_updated,
            "tap_diagnosis": tap_diagnosis,
            "failure_diagnosis": failure_diagnosis,
        }
    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"AI returned invalid JSON: {e}. Try again or switch model.")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI fix call failed: {e}")


class RefineFixRequest(BaseModel):
    """Same context as FixStepsRequest, plus previous fix, user suggestion, and test's fix history."""
    platform: str
    original_steps: list[dict[str, Any]]
    step_results: list[dict[str, Any]]
    failed_step_index: int
    error_message: str = ""
    page_source_xml: str = ""
    page_source_xml_raw: str = ""
    test_name: str = ""
    screenshot_base64: str = ""
    acceptance_criteria: str = ""  # Source of truth
    app_context: str = ""  # App name, package from build metadata
    fix_history: list[dict[str, Any]] = []  # test's stored history from previous runs
    previous_analysis: str = ""
    previous_fixed_steps: list[dict[str, Any]] = []
    previous_changes: list[dict[str, Any]] = []
    user_suggestion: str = ""
    target_platform: str = "android"


@router.post("/api/ai/refine-fix")
async def refine_fix(request: Request, payload: RefineFixRequest) -> dict[str, Any]:
    """Refine the AI fix based on user suggestion, with full context (original steps, step results, error, page source, screenshot, previous fix)."""
    _check_rate_limit(request)
    s = load_settings()
    api_key, model = ai_creds(s)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    failed_step_original = (
        payload.original_steps[payload.failed_step_index]
        if payload.failed_step_index < len(payload.original_steps)
        else {}
    )
    if (
        payload.previous_fixed_steps
        and payload.failed_step_index < len(payload.previous_fixed_steps)
    ):
        failed_step_for_diag = payload.previous_fixed_steps[payload.failed_step_index]
        all_steps_for_diag = payload.previous_fixed_steps
    else:
        failed_step_for_diag = failed_step_original
        all_steps_for_diag = payload.original_steps

    tap_diagnosis, tap_diagnosis_text = _build_tap_diagnosis_for_ai(
        failed_step_for_diag,
        payload.failed_step_index,
        all_steps_for_diag,
        payload.step_results,
        payload.page_source_xml,
        payload.page_source_xml_raw,
        payload.target_platform,
    )

    system_prompt = (
        "You are a senior mobile QA automation engineer. The user has already received an AI-generated fix for a failed Appium test, "
        "but they want to refine it with their own suggestion.\n\n"
        "You have the FULL context:\n"
        "- Original test steps and which passed/failed\n"
        "- Error message and failed step\n"
        "- Page source XML (current screen)\n"
        "- Screenshot of the failure\n"
        "- The PREVIOUS fix (analysis, fixed_steps, changes)\n"
        "- The USER'S SUGGESTION for how to change the fix\n\n"
        "Apply the user's suggestion to refine the fixed_steps. Return the COMPLETE updated fixed_steps list and updated analysis/changes.\n"
        "If acceptance_criteria is provided, your refinement MUST preserve the intended behavior. Do NOT change what the test validates.\n\n"
        "Return ONLY valid JSON:\n"
        '{"analysis": "Updated explanation reflecting the refinement",'
        ' "fixed_steps": [...],'
        ' "data_fixes": {"variable_name": "new_value", ...},'
        ' "changes": [{"step_index": N, "was": "...", "now": "...", "reason": "..."}]}\n\n'
        "CRITICAL DATA RULES (NEVER VIOLATE):\n"
        "1. NEVER hardcode raw test data (emails, phones, passwords, OTPs, URLs, names, amounts, codes, dates) in fixed_steps text/expect fields.\n"
        "2. If the user's suggestion relies on ANY new data value, CREATE a new variable. Add it to data_fixes and use ${new_variable_name} in the step.\n"
        "   - CRITICAL: The dictionary key in data_fixes MUST be the raw variable name ONLY. Do NOT wrap the key in ${}.\n"
        "   - CORRECT: \"data_fixes\": {\"amountToSplit\": 100}\n"
        "   - INCORRECT (WILL FAIL): \"data_fixes\": {\"${amountToSplit}\": 100}\n"
        "3. Existing ${variable_name} references must stay as ${variable_name}. If the value needs changing, update it ONLY in data_fixes (using the raw name as key).\n"
        "Example: if adding a step that types '12345' as OTP, create data_fixes: {\"otp_code\": \"12345\"} and use ${otp_code} in the step text.\n\n"
        "fixed_steps must be the COMPLETE list. No markdown."
        + AI_FIX_CLASSIFICATION_RULES
    )

    # Build contextual rules for refine-fix prompt
    _rf_xml = payload.page_source_xml_raw or payload.page_source_xml or ""
    _rf_desc = payload.error_message + " " + payload.test_name
    if payload.target_platform == "android" and _rf_xml and is_compose_screen(_rf_xml):
        _rf_screen_type = "compose"
    elif payload.target_platform in ("ios", "ios_sim") and _rf_xml and is_swiftui_screen(_rf_xml):
        _rf_screen_type = "swiftui"
    else:
        _rf_screen_type = "native"
    rf_rules_block = build_rules_block(_rf_desc, _rf_xml, payload.target_platform, _rf_screen_type)
    system_prompt += "\n\n" + rf_rules_block

    android_pkg_rf = parse_android_package(payload.app_context or "")
    failure_diagnosis_rf = classify_failure_for_ai_fix(
        failed_step_for_diag,
        payload.error_message,
        payload.page_source_xml_raw,
        payload.page_source_xml,
        payload.target_platform,
        android_pkg_rf,
        tap_diagnosis,
        step_results=payload.step_results,
    )

    user_msg = (
        f"Platform: {payload.platform}\n"
        f"Target automation: {payload.target_platform} (Android UiAutomator2 vs iOS XCUITest)\n"
        f"Test: {payload.test_name}\n"
    )
    if payload.app_context:
        user_msg += f"App context: {payload.app_context}\n"
    user_msg += (
        f"\n=== ORIGINAL STEPS ===\n{json.dumps(payload.original_steps, indent=2)}\n\n"
        f"=== STEP RESULTS ===\n"
    )
    for i, r in enumerate(payload.step_results):
        status = r.get("status", "pending")
        details = r.get("details", "")
        if isinstance(details, dict):
            details = details.get("error", str(details))
        user_msg += f"Step {i}: {status}" + (f" — {details}" if details else "") + "\n"

    user_msg += (
        f"\n=== FAILED AT STEP {payload.failed_step_index} ===\n"
        f"Step definition (current fix at failure index): {json.dumps(failed_step_for_diag, indent=2)}\n"
        f"Error: {payload.error_message}\n\n"
    )
    if tap_diagnosis_text:
        user_msg += tap_diagnosis_text

    user_msg += (
        "\n=== STRUCTURED FAILURE CLASSIFICATION ===\n"
        + build_failure_diagnosis_block(failure_diagnosis_rf)
        + "\n"
    )

    if payload.acceptance_criteria:
        user_msg += f"=== SOURCE OF TRUTH / ACCEPTANCE CRITERIA (DO NOT VIOLATE) ===\n{payload.acceptance_criteria}\n\n"
    if payload.fix_history:
        user_msg += "=== ALREADY TRIED FOR THIS TEST (do NOT repeat) ===\n"
        for i, prev in enumerate(payload.fix_history[:5], 1):
            analysis = prev.get("analysis", "")[:150]
            user_msg += f"Attempt {i}: {analysis}...\n"
        user_msg += "\n"
    user_msg += (
        f"=== CURRENT FIX TO REFINE ===\nAnalysis: {payload.previous_analysis}\n\n"
        f"Previous fixed_steps:\n{json.dumps(payload.previous_fixed_steps, indent=2)}\n\n"
        f"=== USER'S SUGGESTION ===\n{payload.user_suggestion}\n\n"
    )
    if payload.page_source_xml:
        filtered_xml = preprocess_live_xml(
            payload.page_source_xml,
            payload.target_platform or payload.platform,
            description=f"{payload.test_name} {payload.user_suggestion or payload.error_message or ''}",
        )
        user_msg += f"=== PAGE SOURCE (filtered) ===\n{filtered_xml}\n"

    refine_images: list[tuple[str, str]] = []
    if payload.screenshot_base64:
        raw_b64 = payload.screenshot_base64
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        refine_images.append(("failure_screenshot", raw_b64))

    body = _build_gemini_body(system_prompt, user_msg, refine_images, temperature=0.15)

    try:
        parsed = await _gemini_call(api_key, model, body)
        fixed = parsed.get("fixed_steps")
        if not isinstance(fixed, list):
            raise HTTPException(status_code=502, detail="AI did not return fixed_steps[]")
        data_fixes = parsed.get("data_fixes") or {}
        if isinstance(data_fixes, dict):
            data_fixes = {k.replace("${", "").replace("}", ""): v for k, v in data_fixes.items()}

        fixed, extra = enforce_data_layer(fixed, data_fixes)
        data_fixes.update(extra)

        return {
            "analysis": parsed.get("analysis", ""),
            "fixed_steps": fixed,
            "changes": parsed.get("changes", []),
            "data_fixes": data_fixes,
            "tap_diagnosis": tap_diagnosis,
            "failure_diagnosis": failure_diagnosis_rf,
        }
    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"AI returned invalid JSON: {e}. Try again or switch model.")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI refine fix failed: {e}")


# ── AI Edit Steps ─────────────────────────────────────────────────────

class EditStepsRequest(BaseModel):
    platform: str
    current_steps: list[dict[str, Any]]
    instruction: str
    page_source_xml: str = ""
    project_id: Optional[int] = None
    folder_id: Optional[int] = None
    screen_names: list[str] = []
    build_ids: Optional[list[int]] = None


@router.post("/api/ai/edit-steps")
async def edit_steps(request: Request, payload: EditStepsRequest) -> dict[str, Any]:
    _check_rate_limit(request)
    s = load_settings()
    api_key, model = ai_creds(s)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    xml_context, screen_images, screens_for_prompt, raw_xmls = _load_screen_context(
        payload.project_id, payload.folder_id, payload.screen_names,
        payload.build_ids, None, payload.platform, payload.instruction,
    )
    grounded = bool(xml_context)

    if not grounded and payload.page_source_xml:
        live_xml = preprocess_live_xml(payload.page_source_xml, payload.platform, description=payload.instruction)
        if live_xml:
            xml_context = live_xml
            raw_xmls = [payload.page_source_xml]
            grounded = True

    if grounded:
        _edit_xml = "".join(getattr(sc, "xml_snapshot", "") or "" for sc in screens_for_prompt[:3]) if screens_for_prompt else (payload.page_source_xml or "")
        if payload.platform == "android" and _edit_xml and is_compose_screen(_edit_xml):
            _edit_st = "compose"
        elif payload.platform == "ios_sim" and _edit_xml and is_swiftui_screen(_edit_xml):
            _edit_st = "swiftui"
        else:
            _edit_st = "native"
        edit_rules = build_rules_block(payload.instruction, xml_context, payload.platform, _edit_st)
    else:
        edit_rules = ""

    system_prompt = (
        "You are a senior mobile QA engineer.\n"
        "The user has an existing set of Appium test steps and wants to modify them.\n"
        "Apply the user's instruction to the steps and return the updated full list.\n"
    )
    if grounded:
        system_prompt += (
            "You will receive real XML page source from the app under test.\n"
            "You MUST use only selectors that exist in the provided XML. Never invent selectors.\n\n"
        )
    if edit_rules:
        system_prompt += edit_rules + "\n\n"
    system_prompt += (
        "Return ONLY valid JSON:\n"
        '{"steps": [{"type": "<step_type>",'
        ' "selector": {"using":"accessibilityId|id|xpath","value":"..."},'
        ' "text": "...", "ms": 1000, "expect":"...", "meta": {...}}],'
        ' "data_fixes": {"variable_name": "new_value", ...},'
        ' "summary": "Brief description of what was changed"}\n\n'
        "CRITICAL DATA RULES (NEVER VIOLATE):\n"
        "1. NEVER hardcode raw test data (emails, phones, passwords, OTPs, URLs, names, amounts, codes, dates) in steps text/expect fields.\n"
        "2. If the user's instruction relies on ANY new data value, CREATE a new variable. Add it to data_fixes and use ${new_variable_name} in the step.\n"
        "   - CRITICAL: The dictionary key in data_fixes MUST be the raw variable name ONLY. Do NOT wrap the key in ${}.\n"
        "3. Existing ${variable_name} references must stay as ${variable_name}. If the value needs changing, update it ONLY in data_fixes.\n\n"
        "Available step types: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
        "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
        "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
        "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n\n"
        "Return the COMPLETE step list (not just changed ones).\n"
        "No markdown, no explanation outside JSON."
    )
    user_msg = (
        f"Platform: {payload.platform}\n\n"
        f"=== CURRENT STEPS ===\n{json.dumps(payload.current_steps, indent=2)}\n\n"
        f"=== INSTRUCTION ===\n{payload.instruction}"
    )
    if xml_context:
        user_msg += f"\n\nDOM CONTEXT\n==========\n{xml_context}"

    body = _build_gemini_body(system_prompt, user_msg, screen_images, temperature=0.1)
    try:
        parsed = await _gemini_call(api_key, model, body, timeout=30)
        steps = parsed.get("steps")
        if not isinstance(steps, list):
            raise HTTPException(status_code=502, detail="AI did not return steps[]")
        data_fixes = parsed.get("data_fixes") or {}
        if isinstance(data_fixes, dict):
            data_fixes = {k.replace("${", "").replace("}", ""): v for k, v in data_fixes.items()}

        steps, extra = enforce_data_layer(steps, data_fixes)
        data_fixes.update(extra)
        if raw_xmls:
            steps = sanitize_selector_packages(steps, raw_xmls)

        grounding_score = None
        if raw_xmls:
            steps, matched, total = validate_selectors_against_xml(steps, raw_xmls, payload.platform)
            grounding_score = {"matched": matched, "total": total}

        return {
            "steps": steps,
            "summary": parsed.get("summary", ""),
            "data_fixes": data_fixes,
            "grounded": grounded,
            "grounding_score": grounding_score,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI edit failed: {e}")


# ── Validate Test Steps Against Screen Library ───────────────────────


class ValidateTestRequest(BaseModel):
    platform: str
    steps: list[dict[str, Any]]
    project_id: int
    folder_id: Optional[int] = None
    screen_names: list[str] = []
    build_ids: Optional[list[int]] = None


@router.post("/api/ai/validate-test")
async def validate_test(payload: ValidateTestRequest) -> dict[str, Any]:
    """Validate test step selectors against Screen Library XML and check data discipline."""
    _, _, screens_for_prompt, raw_xmls = _load_screen_context(
        payload.project_id, payload.folder_id, payload.screen_names,
        payload.build_ids, None, payload.platform,
    )

    if not raw_xmls:
        raise HTTPException(
            status_code=400,
            detail="No screens found in the library for validation. Capture screens first.",
        )

    annotated, matched, total = validate_selectors_against_xml(
        payload.steps, raw_xmls, payload.platform,
    )

    issues: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []

    from ..runner.tap_debugger import _walk, _collect_suggestions
    import xml.etree.ElementTree as _ET

    all_nodes = []
    for xml_str in raw_xmls:
        try:
            root = _ET.fromstring(xml_str.strip())
            all_nodes.extend(_walk(root))
        except _ET.ParseError:
            pass

    for idx, step in enumerate(annotated):
        if not step.get("_grounded", True):
            sel = step.get("selector", {})
            sel_val = sel.get("value", "?")
            issues.append({
                "step_index": idx,
                "type": "selector_not_found",
                "detail": f"Selector '{sel_val}' not found in any screen XML",
            })
            val_lower = (sel_val or "").lower()
            for n in all_nodes[:500]:
                pool = " ".join([
                    n.attrib.get("resource-id", ""),
                    n.attrib.get("content-desc", ""),
                    n.attrib.get("text", ""),
                    n.attrib.get("name", ""),
                    n.attrib.get("label", ""),
                ]).lower()
                if val_lower and val_lower in pool:
                    sug_list = _collect_suggestions(n.attrib, sel_val, payload.platform)
                    if sug_list:
                        top = sug_list[0]
                        suggestions.append({
                            "step_index": idx,
                            "suggested_selector": {"using": top.strategy, "value": top.value},
                            "confidence": top.score,
                        })
                    break

        stype = (step.get("type") or "").strip()
        text = step.get("text", "")
        if stype in ("type", "clearAndType") and text and "${" not in text:
            if len(text) >= 3:
                issues.append({
                    "step_index": idx,
                    "type": "hardcoded_data",
                    "detail": f"Step text '{text}' looks like test data — should use ${{variable}}",
                })

        expect = step.get("expect", "")
        if stype in ("assertText", "assertTextContains") and expect and "${" not in expect:
            if len(expect) >= 3:
                issues.append({
                    "step_index": idx,
                    "type": "hardcoded_data",
                    "detail": f"Expect value '{expect}' looks like test data — should use ${{variable}}",
                })

    return {
        "valid": len(issues) == 0,
        "grounding_score": matched,
        "total_selectors": total,
        "issues": issues,
        "suggestions": suggestions,
    }


# ── Appium Page Source ────────────────────────────────────────────────

@router.post("/api/appium/page-source")
async def get_page_source() -> dict[str, Any]:
    s = load_settings()
    host = s.get("appium_host", settings.appium_host)
    port = s.get("appium_port", settings.appium_port)
    base = f"http://{host}:{port}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            sessions_resp = await client.get(f"{base}/sessions")
            sessions = sessions_resp.json().get("value", [])
            if not sessions:
                return {"ok": False, "xml": "", "message": "No active Appium session"}
            sid = sessions[0]["id"]
            src_resp = await client.get(f"{base}/session/{sid}/source")
            xml = src_resp.json().get("value", "")
            return {"ok": True, "xml": xml}
    except Exception as e:
        return {"ok": False, "xml": "", "message": str(e)}
