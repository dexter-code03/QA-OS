from __future__ import annotations

import json
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
async def generate_steps(payload: GenerateStepsRequest) -> dict[str, Any]:
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
                xml_context = build_xml_context(screens)
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
        sel_json_key = '{"using":"' + using_choices + '","value":"..."}'
        system_prompt = (
            "You are a mobile QA automation expert generating Appium test steps.\n"
            "You will receive real XML page source from the app under test.\n"
            "You MUST use only selectors (resource-id, content-desc, text, class) that exist in the provided XML. Never invent selectors.\n"
            'Return ONLY valid JSON: {"steps": [{"type": "<step_type>",'
            ' "selector": ' + sel_json_key + ","
            ' "text": "...", "ms": 1000, "expect":"...", "meta": {...}}]}\n\n'
            "Available step types:\n"
            "  Tapping: tap, doubleTap, longPress, tapByCoordinates (meta.x, meta.y)\n"
            "  Text: type, clear, clearAndType\n"
            "  Wait: wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled\n"
            "  Gesture: swipe, scroll (text=direction; scroll optionally has selector to scroll-until-visible)\n"
            "  Assert: assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute (meta.attribute + expect)\n"
            "  Keyboard: pressKey (text=key), keyboardAction (legacy alias), hideKeyboard\n"
            "  App: launchApp, closeApp, resetApp (text=bundleId/package, optional)\n"
            "  Capture: takeScreenshot, getPageSource\n\n"
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
        user_msg = f"Platform: {payload.platform}\nTest objective:\n{payload.prompt}\n\nDOM CONTEXT\n==========\n{xml_context}"
    else:
        compose_from_live_xml = (
            payload.platform == "android"
            and bool(payload.page_source_xml.strip())
            and is_compose_screen(payload.page_source_xml)
        )
        swiftui_from_live_xml = (
            payload.platform == "ios_sim"
            and bool(payload.page_source_xml.strip())
            and is_swiftui_screen(payload.page_source_xml)
        )
        sel_json_key_ng = '{"using":"' + using_choices + '","value":"..."}'
        system_prompt = (
            "You are a senior mobile QA automation engineer.\n"
            "Return ONLY valid JSON with this shape:\n"
            '{"steps": [{"type": "<step_type>",'
            ' "selector": ' + sel_json_key_ng + ","
            ' "text": "...", "ms": 1000, "expect":"...", "meta": {...}}]}\n'
            "Available step types:\n"
            "  Tapping: tap, doubleTap, longPress, tapByCoordinates (meta.x, meta.y)\n"
            "  Text: type, clear, clearAndType\n"
            "  Wait: wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled\n"
            "  Gesture: swipe, scroll (text=direction)\n"
            "  Assert: assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute (meta.attribute + expect)\n"
            "  Keyboard: pressKey (text=key), keyboardAction (legacy alias), hideKeyboard\n"
            "  App: launchApp, closeApp, resetApp (text=bundleId/package, optional)\n"
            "  Capture: takeScreenshot, getPageSource\n\n"
            "IMPORTANT: For keyboard keys (return, done, go, next, search), use pressKey or keyboardAction instead of tap.\n"
            "Use hideKeyboard when you need to dismiss the keyboard without pressing a specific key.\n"
            "Keep selectors realistic for Appium. Use accessibilityId where possible.\n"
            "No markdown, no explanation, only JSON."
        )
        if compose_from_live_xml:
            system_prompt += (
                "\n\nJETPACK COMPOSE (detected from XML): NEVER use selector.using \"id\" for taps/types on this UI. "
                'ALWAYS use "-android uiautomator" with UiSelector Java, e.g. '
                'new UiSelector().resourceId("com.app:id/foo"), or .descriptionContains("..."), .textContains("...").\n'
            )
        if swiftui_from_live_xml:
            system_prompt += (
                "\n\nSWIFTUI (detected from XML): Prefer \"-ios predicate string\" with name == 'id' or label CONTAINS '...'. "
                "Avoid selector.using \"id\" for taps. Use \"-ios class chain\" to disambiguate (e.g. **/XCUIElementTypeButton[`name == 'x'`]). "
                "Avoid xpath on iOS unless necessary.\n"
            )
        user_msg = f"Platform: {payload.platform}\nGoal:\n{payload.prompt}"
        if payload.page_source_xml:
            user_msg += f"\n\nCurrent page source XML:\n{payload.page_source_xml[:8000]}"

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
            return {"steps": steps, "grounded": grounded, "screens_used": screens_used}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI generate steps failed: {e}")


# ── AI Generate Test Suite (bulk) ─────────────────────────────────────

class GenerateSuiteRequest(BaseModel):
    platform: str
    prompt: str
    page_source_xml: str = ""
    project_id: int
    suite_id: int
    folder_id: Optional[int] = None
    build_ids: Optional[list[int]] = None


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
                xml_context = build_xml_context(screens)
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
        sel_tc = '{"using":"' + using_choices_suite + '","value":"..."}'
        system_prompt = (
            "You are a senior mobile QA automation engineer.\n"
            "Generate MULTIPLE test cases for a test suite. Each test case should cover a different scenario.\n"
            "You will receive real XML page source and screenshots from the app under test.\n"
            "You MUST use only selectors (resource-id, content-desc, text, class) that exist in the provided XML. Never invent selectors.\n"
            "Return ONLY valid JSON with this shape:\n"
            '{"test_cases": [{"name": "Test case name", "acceptance_criteria": "What this test must validate (expected outcome, fail conditions)", "steps": [{"type": "<step_type>",'
            ' "selector": ' + sel_tc + ', "text": "...", "ms": 1000, "expect":"...", "meta": {...}}]}, ...]}\n'
            "Available step types: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
            "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
            "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
            "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n\n"
            + android_rules
            + ios_rules
            + "SELECTOR PRIORITY ORDER for native Android / iOS (use first match; follow per-screen Compose rules above when applicable):\n"
            "1. resource-id (most stable)\n"
            "2. content-desc / accessibility id (stable)\n"
            "3. text (fragile — only if no ID available)\n"
            "4. xpath (last resort — only if nothing else exists)\n"
            "Generate 3-8 test cases covering happy path, edge cases, and error scenarios.\n"
            "For each test case, include acceptance_criteria: a brief statement of what the test validates and when it should pass/fail.\n"
            "For keyboard keys (return, done, go), use pressKey or keyboardAction. Use hideKeyboard when needed.\n"
            "Every selector you use must be found verbatim in the XML below.\n"
            "No markdown, no explanation, only JSON."
        )
        user_msg = f"Platform: {payload.platform}\n\nDescribe the feature/suite to test:\n{payload.prompt}\n\nDOM CONTEXT\n==========\n{xml_context}"
    else:
        compose_from_live_xml = (
            payload.platform == "android"
            and bool((payload.page_source_xml or "").strip())
            and is_compose_screen(payload.page_source_xml)
        )
        swiftui_from_live_xml = (
            payload.platform == "ios_sim"
            and bool((payload.page_source_xml or "").strip())
            and is_swiftui_screen(payload.page_source_xml)
        )
        sel_tc_ng = '{"using":"' + using_choices_suite + '","value":"..."}'
        system_prompt = (
            "You are a senior mobile QA automation engineer.\n"
            "Generate MULTIPLE test cases for a test suite. Each test case should cover a different scenario.\n"
            "Return ONLY valid JSON with this shape:\n"
            '{"test_cases": [{"name": "Test case name", "acceptance_criteria": "What this test must validate (expected outcome, fail conditions)", "steps": [{"type": "<step_type>",'
            ' "selector": ' + sel_tc_ng + ', "text": "...", "ms": 1000, "expect":"...", "meta": {...}}]}, ...]}\n'
            "Available step types: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
            "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
            "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
            "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n\n"
            "Generate 3-8 test cases covering happy path, edge cases, and error scenarios.\n"
            "For each test case, include acceptance_criteria: a brief statement of what the test validates and when it should pass/fail.\n"
            "For keyboard keys (return, done, go), use pressKey or keyboardAction. Use hideKeyboard when needed.\n"
            "No markdown, no explanation, only JSON."
        )
        if compose_from_live_xml:
            system_prompt += (
                "\n\nJETPACK COMPOSE (detected from XML): NEVER use selector.using \"id\" for taps/types on this UI. "
                'ALWAYS use "-android uiautomator" with UiSelector Java.\n'
            )
        if swiftui_from_live_xml:
            system_prompt += (
                "\n\nSWIFTUI (detected from XML): Prefer \"-ios predicate string\" and \"-ios class chain\"; avoid bare \"id\" and avoid xpath unless necessary.\n"
            )
        user_msg = f"Platform: {payload.platform}\n\nDescribe the feature/suite to test:\n{payload.prompt}"
        if payload.page_source_xml:
            user_msg += f"\n\nCurrent page source XML:\n{payload.page_source_xml[:8000]}"

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

        created: list[dict] = []
        with SessionLocal() as db:
            p = db.query(Project).filter(Project.id == payload.project_id).first()
            if not p:
                raise HTTPException(status_code=404, detail="Project not found")
            s = db.query(TestSuite).filter(TestSuite.id == payload.suite_id).first()
            if not s:
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

        return {"created": len(created), "test_cases": created}
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
    page_source_xml: str = ""  # simplified for LLM; may not be strict XML
    page_source_xml_raw: str = ""  # raw Appium XML for tap diagnosis (recommended)
    test_name: str = ""
    screenshot_base64: str = ""
    already_tried_fixes: list[dict[str, Any]] = []  # [{analysis, fixed_steps}, ...] from test.fix_history
    acceptance_criteria: str = ""  # Source of truth: what this test must validate (from test definition)
    app_context: str = ""  # App name, package, etc. from build metadata for correct context
    target_platform: str = "android"  # slot the fixed steps will be saved into


@router.post("/api/ai/fix-steps")
async def fix_steps(payload: FixStepsRequest) -> dict[str, Any]:
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
    )

    passed_steps = []
    for i, r in enumerate(payload.step_results):
        if r.get("status") == "passed" and i < len(payload.original_steps):
            passed_steps.append({"index": i, "step": payload.original_steps[i]})

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
        ' "fixed_steps": [{"type": "<step_type>",'
        ' "selector": {"using":"accessibilityId|id|xpath","value":"..."},'
        ' "text": "...", "ms": 1000, "expect":"...", "meta": {...}}],'
        ' "changes": [{"step_index": 0, "was": "...", "now": "...", "reason": "..."}]}\n\n'
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
    user_msg += (
        f"\n=== ORIGINAL STEPS ===\n{json.dumps(payload.original_steps, indent=2)}\n\n"
        f"=== STEP RESULTS ===\n"
    )
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

    if payload.page_source_xml:
        user_msg += f"\n=== PAGE SOURCE XML (current screen) ===\n{payload.page_source_xml[:12000]}\n"

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
            return {
                "analysis": parsed.get("analysis", ""),
                "fixed_steps": fixed,
                "changes": parsed.get("changes", []),
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
async def refine_fix(payload: RefineFixRequest) -> dict[str, Any]:
    """Refine the AI fix based on user suggestion, with full context (original steps, step results, error, page source, screenshot, previous fix)."""
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
        ' "changes": [{"step_index": N, "was": "...", "now": "...", "reason": "..."}]}\n\n'
        "fixed_steps must be the COMPLETE list. No markdown."
        + AI_FIX_CLASSIFICATION_RULES
    )

    android_pkg_rf = parse_android_package(payload.app_context or "")
    failure_diagnosis_rf = classify_failure_for_ai_fix(
        failed_step_for_diag,
        payload.error_message,
        payload.page_source_xml_raw,
        payload.page_source_xml,
        payload.target_platform,
        android_pkg_rf,
        tap_diagnosis,
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
        user_msg += f"=== PAGE SOURCE XML ===\n{payload.page_source_xml[:12000]}\n"

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
            return {
                "analysis": parsed.get("analysis", ""),
                "fixed_steps": fixed,
                "changes": parsed.get("changes", []),
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
async def edit_steps(payload: EditStepsRequest) -> dict[str, Any]:
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
        ' "summary": "Brief description of what was changed"}\n\n'
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
            return {"steps": steps, "summary": parsed.get("summary", "")}
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
