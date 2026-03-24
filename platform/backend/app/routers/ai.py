from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any, Optional

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
from ..helpers_xml import build_xml_context_v2, preprocess_live_xml
from ..models import Build, Project, ScreenLibrary, TestDefinition, TestSuite
from ..runner.ai_fix_diagnosis import (
    AI_FIX_CLASSIFICATION_RULES,
    build_failure_diagnosis_block,
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

    xml_context = ""
    grounded = False
    screen_images: list[tuple[str, str]] = []  # (name, base64_png)
    screens_for_prompt: list[ScreenLibrary] = []

    if payload.project_id and (payload.folder_id or payload.screen_names):
        with SessionLocal() as db:
            if payload.folder_id:
                q = db.query(ScreenLibrary).filter(
                    ScreenLibrary.folder_id == payload.folder_id,
                    ScreenLibrary.platform == payload.platform,
                )
            else:
                q = db.query(ScreenLibrary).filter(
                    ScreenLibrary.project_id == payload.project_id,
                    ScreenLibrary.platform == payload.platform,
                    ScreenLibrary.name.in_(payload.screen_names),
                )
            q = filter_screen_library_by_build(q, payload.build_ids, payload.build_id)
            screens = q.all()
            if screens:
                screens_for_prompt = list(screens)
                xml_context = build_xml_context_v2(screens, description=payload.prompt)
                grounded = True
                for scr in screens:
                    if scr.screenshot_path:
                        fpath = settings.artifacts_dir / str(scr.project_id) / scr.screenshot_path
                        if fpath.exists():
                            img_b64 = compress_screenshot(fpath)
                            if img_b64:
                                screen_images.append((scr.name, img_b64))

    using_choices = (
        "accessibilityId|id|xpath|-android uiautomator"
        if payload.platform == "android"
        else "accessibilityId|id|xpath|-ios predicate string|-ios class chain"
    )

    if grounded and xml_context:
        android_rules = android_selector_generation_rules(screens_for_prompt) if payload.platform == "android" else ""
        ios_rules = ios_selector_generation_rules(screens_for_prompt) if payload.platform == "ios_sim" else ""

        # Detect screen_type for contextual rule injection
        _any_xml = "".join(getattr(s, "xml_snapshot", "") or "" for s in screens_for_prompt[:3])
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
        user_msg = f"Platform: {payload.platform}\nTest objective:\n{payload.prompt}{var_hint}\n\nDOM CONTEXT\n==========\n{xml_context}"
    else:
        _live_xml = (payload.page_source_xml or "").strip()
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
        user_msg = f"Platform: {payload.platform}\nGoal:\n{payload.prompt}{var_hint}"
        if live_xml_str:
            user_msg += f"\n\nCurrent page source (filtered):\n{live_xml_str}"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gemini_headers = {"x-goog-api-key": api_key}
    parts: list[dict[str, Any]] = [{"text": f"{system_prompt}\n\n{user_msg}"}]
    for img_name, img_b64 in screen_images[:6]:
        parts.append({"text": f"\n[Screenshot: {img_name}]"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.15 if grounded else 0.2, "responseMimeType": "application/json"},
    }

    screens_used = len(screen_images) if grounded else 0
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body, headers=gemini_headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"AI API error: {resp.text[:300]}")
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            steps = parsed.get("steps")
            if not isinstance(steps, list):
                raise HTTPException(status_code=502, detail="AI did not return steps[]")

            # Auto-extract data layer from AI response or fallback
            test_data = parsed.get("test_data") or {}
            data_set_id: int | None = None
            if not test_data:
                from ..helpers_data_extraction import extract_variables_from_steps
                steps, test_data = extract_variables_from_steps(steps)
            if test_data and payload.project_id:
                data_set_id = _auto_create_data_layer(
                    payload.project_id,
                    payload.prompt[:60].strip() or "AI Generated",
                    test_data,
                )

            return {"steps": steps, "grounded": grounded, "screens_used": screens_used, "data_set_id": data_set_id, "test_data": test_data}
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


@router.post("/api/ai/generate-suite")
async def generate_suite(payload: GenerateSuiteRequest) -> dict[str, Any]:
    if payload.platform not in ("android", "ios_sim"):
        raise HTTPException(status_code=400, detail="platform must be android|ios_sim")

    s = load_settings()
    api_key, model = ai_creds(s)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    xml_context = ""
    screen_images: list[tuple[str, str]] = []
    screens_for_prompt: list[ScreenLibrary] = []
    if payload.folder_id:
        with SessionLocal() as db:
            q = db.query(ScreenLibrary).filter(
                ScreenLibrary.folder_id == payload.folder_id,
                ScreenLibrary.platform == payload.platform,
            )
            q = filter_screen_library_by_build(q, payload.build_ids, None)
            screens = q.all()
            if screens:
                screens_for_prompt = list(screens)
                xml_context = build_xml_context_v2(screens, description=payload.prompt)
                for scr in screens:
                    if scr.screenshot_path:
                        fpath = settings.artifacts_dir / str(scr.project_id) / scr.screenshot_path
                        if fpath.exists():
                            img_b64 = compress_screenshot(fpath)
                            if img_b64:
                                screen_images.append((scr.name, img_b64))

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

        sel_tc = '{"using":"' + using_choices_suite + '","value":"..."}'
        system_prompt = (
            "You are a senior mobile QA automation engineer.\n"
            "Generate MULTIPLE test cases for a test suite. Each test case should cover a different scenario.\n"
            "You will receive real XML page source and screenshots from the app under test.\n"
            "You MUST use only selectors (resource-id, content-desc, text, class) that exist in the provided XML. Never invent selectors.\n"
            "Return ONLY valid JSON with this shape:\n"
            '{"test_cases": [{"name": "...", "acceptance_criteria": "...", "steps": [{"type": "<step_type>",'
            ' "selector": ' + sel_tc + ', "text": "...", "ms": 1000, "expect":"...", "meta": {...}}]}, ...],'
            ' "test_data": {"variable_name": "value", ...}}\n'
            "IMPORTANT: Extract ALL test data (emails, phones, passwords, OTPs, URLs, names, amounts) into the test_data object. "
            "Use ${variable_name} syntax in step text/expect fields instead of hardcoded values.\n"
            "Available step types: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
            "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
            "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
            "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n\n"
            + suite_rules_block + "\n\n"
            + android_rules
            + ios_rules
            + "SELECTOR PRIORITY ORDER for native Android / iOS (use first match; follow per-screen Compose rules above when applicable):\n"
            "1. resource-id (most stable)\n"
            "2. content-desc / accessibility id (stable)\n"
            "3. text (fragile — only if no ID available)\n"
            "4. xpath (last resort — only if nothing else exists)\n"
            "For each test case, include acceptance_criteria: a brief statement of what the test validates and when it should pass/fail.\n"
            "For keyboard keys (return, done, go), use pressKey or keyboardAction. Use hideKeyboard when needed.\n"
            "Every selector you use must be found verbatim in the XML below.\n"
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
            system_prompt += "\nGenerate 3-8 test cases covering happy path, edge cases, and error scenarios.\n"

        var_hint_suite = _variable_hint(payload.project_id)
        user_msg = f"Platform: {payload.platform}\n\nDescribe the feature/suite to test:\n{payload.prompt}{var_hint_suite}\n\nDOM CONTEXT\n==========\n{xml_context}"
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
            "You are a senior mobile QA automation engineer.\n"
            "Generate MULTIPLE test cases for a test suite. Each test case should cover a different scenario.\n"
            "Return ONLY valid JSON with this shape:\n"
            '{"test_cases": [{"name": "...", "acceptance_criteria": "...", "steps": [{"type": "<step_type>",'
            ' "selector": ' + sel_tc_ng + ', "text": "...", "ms": 1000, "expect":"...", "meta": {...}}]}, ...],'
            ' "test_data": {"variable_name": "value", ...}}\n'
            "IMPORTANT: Extract ALL test data into the test_data object. Use ${variable_name} in step text/expect fields.\n"
            "Available step types: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
            "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
            "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
            "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n\n"
            + suite_rules_ng + "\n\n"
            "For each test case, include acceptance_criteria: a brief statement of what the test validates and when it should pass/fail.\n"
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
            system_prompt += "\nGenerate 3-8 test cases covering happy path, edge cases, and error scenarios.\n"

        var_hint_suite_ng = _variable_hint(payload.project_id)
        user_msg = f"Platform: {payload.platform}\n\nDescribe the feature/suite to test:\n{payload.prompt}{var_hint_suite_ng}"
        if suite_live_xml_str:
            user_msg += f"\n\nCurrent page source (filtered):\n{suite_live_xml_str}"
        if payload.manual_tests:
            user_msg += f"\n\nMANUAL TEST CASES TO TRANSLATE:\n{_format_manual_tests(payload.manual_tests)}"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gemini_headers = {"x-goog-api-key": api_key}
    parts_list: list[dict[str, Any]] = [{"text": f"{system_prompt}\n\n{user_msg}"}]
    for img_name, img_b64 in screen_images[:6]:
        parts_list.append({"text": f"\n[Screenshot: {img_name}]"})
        parts_list.append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})
    body = {
        "contents": [{"parts": parts_list}],
        "generationConfig": {"temperature": 0.15 if grounded else 0.3, "responseMimeType": "application/json"},
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body, headers=gemini_headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"AI API error: {resp.text[:300]}")
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            raw_cases = parsed.get("test_cases")
            if not isinstance(raw_cases, list):
                raise HTTPException(status_code=502, detail="AI did not return test_cases[]")

        # Extract test_data and auto-create data layer
        suite_test_data = parsed.get("test_data") or {}
        if not suite_test_data:
            from ..helpers_data_extraction import extract_variables_from_steps
            all_steps = [s for tc in raw_cases if isinstance(tc.get("steps"), list) for s in tc["steps"]]
            _, suite_test_data = extract_variables_from_steps(all_steps)

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

        return {"created": len(created), "test_cases": created, "data_set_id": suite_data_set_id}
    except HTTPException:
        raise
    except Exception as e:
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
        "5. Keep steps that passed unchanged. Only fix the failed step and any subsequent steps that need updating.\n\n"
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
        ' "fix_type": "step|data|both",'
        ' "fixed_steps": [{"type": "<step_type>",'
        ' "selector": {"using":"accessibilityId|id|xpath","value":"..."},'
        ' "text": "...", "ms": 1000, "expect":"...", "meta": {...}}],'
        ' "data_fixes": {"variable_name": "new_value", ...},'
        ' "changes": [{"step_index": 0, "was": "...", "now": "...", "reason": "..."}]}\n\n'
        "fix_type: 'step' = fix selector/structure only, 'data' = update variable values only, 'both' = fix steps AND update data.\n"
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

    if payload.page_source_xml:
        filtered_xml = preprocess_live_xml(
            payload.page_source_xml,
            payload.platform,
            description=payload.error_message or "",
        )
        user_msg += f"\n=== PAGE SOURCE (filtered) ===\n{filtered_xml}\n"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gemini_headers = {"x-goog-api-key": api_key}

    parts: list[dict] = [{"text": f"{system_prompt}\n\n{user_msg}"}]
    if payload.screenshot_base64:
        raw = payload.screenshot_base64
        if "," in raw:
            raw = raw.split(",", 1)[1]
        parts.append({"inlineData": {"mimeType": "image/png", "data": raw}})

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body, headers=gemini_headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"AI API error: {resp.text[:300]}")
            data = resp.json()
            text = gemini_extract_text(data)
            parsed = json.loads(text)
            fixed = parsed.get("fixed_steps")
            if not isinstance(fixed, list):
                raise HTTPException(status_code=502, detail="AI did not return fixed_steps[]")

            # Auto-apply data fixes to DataSet
            fix_type = parsed.get("fix_type", "step")
            data_fixes = parsed.get("data_fixes") or {}
            if isinstance(data_fixes, dict):
                data_fixes = {k.replace("${", "").replace("}", ""): v for k, v in data_fixes.items()}
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
            description=payload.user_suggestion or payload.error_message or "",
        )
        user_msg += f"=== PAGE SOURCE (filtered) ===\n{filtered_xml}\n"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gemini_headers = {"x-goog-api-key": api_key}
    parts: list[dict] = [{"text": f"{system_prompt}\n\n{user_msg}"}]
    if payload.screenshot_base64:
        raw = payload.screenshot_base64
        if "," in raw:
            raw = raw.split(",", 1)[1]
        parts.append({"inlineData": {"mimeType": "image/png", "data": raw}})

    body = {"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body, headers=gemini_headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"AI API error: {resp.text[:300]}")
            data = resp.json()
            text = gemini_extract_text(data)
            parsed = json.loads(text)
            fixed = parsed.get("fixed_steps")
            if not isinstance(fixed, list):
                raise HTTPException(status_code=502, detail="AI did not return fixed_steps[]")
            data_fixes = parsed.get("data_fixes") or {}
            if isinstance(data_fixes, dict):
                data_fixes = {k.replace("${", "").replace("}", ""): v for k, v in data_fixes.items()}
                
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


@router.post("/api/ai/edit-steps")
async def edit_steps(request: Request, payload: EditStepsRequest) -> dict[str, Any]:
    _check_rate_limit(request)
    s = load_settings()
    api_key, model = ai_creds(s)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    system_prompt = (
        "You are a senior mobile QA engineer.\n"
        "The user has an existing set of Appium test steps and wants to modify them.\n"
        "Apply the user's instruction to the steps and return the updated full list.\n\n"
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
        "   - CORRECT: \"data_fixes\": {\"amountToSplit\": 100}\n"
        "   - INCORRECT (WILL FAIL): \"data_fixes\": {\"${amountToSplit}\": 100}\n"
        "3. Existing ${variable_name} references must stay as ${variable_name}. If the value needs changing, update it ONLY in data_fixes (using the raw name as key).\n"
        "Example: if adding a step that types '12345' as OTP, create data_fixes: {\"otp_code\": \"12345\"} and use ${otp_code} in the step text.\n\n"
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

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gemini_headers = {"x-goog-api-key": api_key}
    body = {
        "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_msg}"}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body, headers=gemini_headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"AI API error: {resp.text[:300]}")
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            steps = parsed.get("steps")
            if not isinstance(steps, list):
                raise HTTPException(status_code=502, detail="AI did not return steps[]")
            data_fixes = parsed.get("data_fixes") or {}
            if isinstance(data_fixes, dict):
                data_fixes = {k.replace("${", "").replace("}", ""): v for k, v in data_fixes.items()}
                
            return {
                "steps": steps,
                "summary": parsed.get("summary", ""),
                "data_fixes": data_fixes
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI edit failed: {e}")


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
