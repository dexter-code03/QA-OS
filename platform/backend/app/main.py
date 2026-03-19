from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import uuid
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .db import SessionLocal, init_db, Project, Module, TestSuite, Build, TestDefinition, Run
from .events import event_bus, RunEvent
from .schemas import (
    ProjectCreate,
    ProjectOut,
    ModuleCreate,
    ModuleOut,
    SuiteCreate,
    SuiteOut,
    BuildOut,
    TestCreate,
    TestUpdate,
    TestOut,
    RunCreate,
    RunOut,
)
from .settings import settings, ensure_dirs
from .runner.engine import run_engine
from pydantic import BaseModel


app = FastAPI(title="QA Platform (Local Appium TestOps)", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SETTINGS_FILE = settings.data_dir / "settings.json"
ONBOARDING_FILE = settings.data_dir / "onboarding.json"


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return {}


def _ai_creds(s: dict | None = None) -> tuple[str, str]:
    if s is None:
        s = _load_settings()
    key = s.get("ai_api_key") or s.get("ai_key") or ""
    model = s.get("ai_model") or "gemini-2.5-flash"
    return key, model


def _save_settings(data: dict) -> None:
    ensure_dirs()
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))


@app.on_event("startup")
async def _startup() -> None:
    ensure_dirs()
    init_db()
    run_engine.start()


def _run_to_out(r: Run) -> RunOut:
    return RunOut(
        id=r.id,
        project_id=r.project_id,
        build_id=r.build_id,
        test_id=r.test_id,
        status=r.status,
        platform=r.platform,
        device_target=r.device_target,
        started_at=r.started_at,
        finished_at=r.finished_at,
        error_message=r.error_message,
        summary=r.summary or {},
        artifacts=r.artifacts or {},
    )


# ── Health ──────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


# ── Settings & Onboarding ──────────────────────────────────────────────

@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return _load_settings()


@app.post("/api/settings")
def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    current = _load_settings()
    current.update(payload)
    _save_settings(current)
    return current


@app.get("/api/onboarding")
def onboarding_status() -> dict[str, Any]:
    if ONBOARDING_FILE.exists():
        return json.loads(ONBOARDING_FILE.read_text())
    return {"completed": False}


@app.post("/api/onboarding/complete")
def onboarding_complete() -> dict[str, Any]:
    ensure_dirs()
    data = {"completed": True, "completed_at": datetime.utcnow().isoformat()}
    ONBOARDING_FILE.write_text(json.dumps(data))
    return data


# ── Devices ────────────────────────────────────────────────────────────

@app.get("/api/devices")
def list_devices() -> dict[str, Any]:
    android_devices: list[dict] = []
    ios_simulators: list[dict] = []

    try:
        out = subprocess.check_output(["adb", "devices", "-l"], text=True, timeout=5)
        for line in out.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                info = {"serial": parts[0], "type": "device"}
                for p in parts[2:]:
                    if ":" in p:
                        k, v = p.split(":", 1)
                        info[k] = v
                android_devices.append(info)
    except Exception:
        pass

    try:
        # Use full path to xcrun for reliability when backend runs from different env (e.g. IDE)
        xcrun = "/usr/bin/xcrun" if os.path.exists("/usr/bin/xcrun") else "xcrun"
        out = subprocess.check_output(
            [xcrun, "simctl", "list", "devices", "--json"], text=True, timeout=15
        )
        data = json.loads(out)
        for runtime, devs in data.get("devices", {}).items():
            for d in devs:
                if d.get("isAvailable"):
                    ios_simulators.append({
                        "udid": d["udid"],
                        "name": d["name"],
                        "state": d.get("state", ""),
                        "runtime": runtime.split(".")[-1] if "." in runtime else runtime,
                    })
    except Exception as e:
        import logging
        logging.getLogger("uvicorn.error").warning(f"iOS device detection failed: {e}")

    return {"android": android_devices, "ios_simulators": ios_simulators}


# ── Connection Tests ───────────────────────────────────────────────────

@app.post("/api/test-connection/appium")
async def test_appium() -> dict[str, Any]:
    s = _load_settings()
    host = s.get("appium_host", settings.appium_host)
    port = s.get("appium_port", settings.appium_port)
    url = f"http://{host}:{port}/status"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
            if r.status_code == 200:
                return {"ok": True, "message": f"Appium is running at {host}:{port}"}
            return {"ok": False, "message": f"Appium returned HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "message": f"Cannot reach Appium at {host}:{port}: {e}"}


@app.post("/api/test-connection/confluence")
async def test_confluence() -> dict[str, Any]:
    s = _load_settings()
    url = s.get("confluence_url", "")
    token = s.get("confluence_token", "")
    if not url:
        return {"ok": False, "message": "No Confluence URL configured"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            r = await client.get(url.rstrip("/") + "/rest/api/space?limit=1", headers=headers)
            if r.status_code == 200:
                return {"ok": True, "message": "Connected to Confluence"}
            return {"ok": False, "message": f"Confluence returned HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "message": f"Cannot reach Confluence: {e}"}


# ── AI Connection Test ─────────────────────────────────────────────────

@app.post("/api/test-connection/ai")
async def test_ai() -> dict[str, Any]:
    s = _load_settings()
    api_key, model = _ai_creds(s)
    if not api_key:
        return {"ok": False, "message": "No AI API key configured"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}?key={api_key}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url)
            if r.status_code == 200:
                return {"ok": True, "message": f"Connected to {model}"}
            return {"ok": False, "message": f"API returned HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "message": f"Cannot reach AI API: {e}"}


# ── AI Step Generation ─────────────────────────────────────────────────

class GenerateStepsRequest(BaseModel):
    platform: str
    prompt: str
    page_source_xml: str = ""


@app.post("/api/ai/generate-steps")
async def generate_steps(payload: GenerateStepsRequest) -> dict[str, Any]:
    if payload.platform not in ("android", "ios_sim"):
        raise HTTPException(status_code=400, detail="platform must be android|ios_sim")

    s = _load_settings()
    api_key, model = _ai_creds(s)

    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    system_prompt = (
        "You are a senior mobile QA automation engineer.\n"
        "Return ONLY valid JSON with this shape:\n"
        '{"steps": [{"type": "tap|type|wait|waitForVisible|assertText|assertVisible|takeScreenshot|swipe|keyboardAction|hideKeyboard",'
        ' "selector": {"using":"accessibilityId|id|xpath","value":"..."},'
        ' "text": "...", "ms": 1000, "expect":"..."}]}\n'
        "IMPORTANT: For keyboard keys (return, done, go, next, search), use keyboardAction instead of tap.\n"
        "Use hideKeyboard when you need to dismiss the keyboard without pressing a specific key.\n"
        "Keep selectors realistic for Appium. Use accessibilityId where possible.\n"
        "No markdown, no explanation, only JSON."
    )
    user_msg = f"Platform: {payload.platform}\nGoal:\n{payload.prompt}"
    if payload.page_source_xml:
        user_msg += f"\n\nCurrent page source XML:\n{payload.page_source_xml[:8000]}"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_msg}"}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"AI API error: {resp.text[:300]}")
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            steps = parsed.get("steps")
            if not isinstance(steps, list):
                raise HTTPException(status_code=502, detail="AI did not return steps[]")
            return {"steps": steps}
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


@app.post("/api/ai/generate-suite")
async def generate_suite(payload: GenerateSuiteRequest) -> dict[str, Any]:
    if payload.platform not in ("android", "ios_sim"):
        raise HTTPException(status_code=400, detail="platform must be android|ios_sim")

    s = _load_settings()
    api_key, model = _ai_creds(s)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    system_prompt = (
        "You are a senior mobile QA automation engineer.\n"
        "Generate MULTIPLE test cases for a test suite. Each test case should cover a different scenario.\n"
        "Return ONLY valid JSON with this shape:\n"
        '{"test_cases": [{"name": "Test case name", "acceptance_criteria": "What this test must validate (expected outcome, fail conditions)", "steps": [{"type": "tap|type|wait|waitForVisible|assertText|assertVisible|takeScreenshot|swipe|keyboardAction|hideKeyboard",'
        ' "selector": {"using":"accessibilityId|id|xpath","value":"..."}, "text": "...", "ms": 1000, "expect":"..."}]}, ...]}\n'
        "Generate 3-8 test cases covering happy path, edge cases, and error scenarios.\n"
        "For each test case, include acceptance_criteria: a brief statement of what the test validates and when it should pass/fail.\n"
        "For keyboard keys (return, done, go), use keyboardAction. Use hideKeyboard when needed.\n"
        "No markdown, no explanation, only JSON."
    )
    user_msg = f"Platform: {payload.platform}\n\nDescribe the feature/suite to test:\n{payload.prompt}"
    if payload.page_source_xml:
        user_msg += f"\n\nCurrent page source XML:\n{payload.page_source_xml[:8000]}"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_msg}"}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body)
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
                t = TestDefinition(project_id=payload.project_id, suite_id=payload.suite_id, name=name, steps=steps, acceptance_criteria=ac)
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

class FixStepsRequest(BaseModel):
    platform: str
    original_steps: list[dict[str, Any]]
    step_results: list[dict[str, Any]]
    failed_step_index: int
    error_message: str = ""
    page_source_xml: str = ""
    test_name: str = ""
    screenshot_base64: str = ""
    already_tried_fixes: list[dict[str, Any]] = []  # [{analysis, fixed_steps}, ...] from test.fix_history
    acceptance_criteria: str = ""  # Source of truth: what this test must validate (from test definition)
    app_context: str = ""  # App name, package, etc. from build metadata for correct context


@app.post("/api/ai/fix-steps")
async def fix_steps(payload: FixStepsRequest) -> dict[str, Any]:
    s = _load_settings()
    api_key, model = _ai_creds(s)

    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    failed_step = payload.original_steps[payload.failed_step_index] if payload.failed_step_index < len(payload.original_steps) else {}

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
        "- tap, type, wait, waitForVisible, assertText, assertVisible, takeScreenshot, swipe\n"
        "- keyboardAction (text = key name: return|done|go|next|search|send)\n"
        "- hideKeyboard (no selector needed)\n\n"
        "Return ONLY valid JSON with this shape:\n"
        '{"analysis": "Brief explanation of what went wrong and how you fixed it",'
        ' "fixed_steps": [{"type": "tap|type|wait|waitForVisible|assertText|assertVisible|takeScreenshot|swipe|keyboardAction|hideKeyboard",'
        ' "selector": {"using":"accessibilityId|id|xpath","value":"..."},'
        ' "text": "...", "ms": 1000, "expect":"..."}],'
        ' "changes": [{"step_index": 0, "was": "...", "now": "...", "reason": "..."}]}\n\n'
        "IMPORTANT: fixed_steps must be the COMPLETE list (all steps, not just changed ones).\n"
        "Use accessibilityId or resource-id where possible, xpath as fallback.\n"
        "No markdown, no explanation outside the JSON."
    )

    user_msg = (
        f"Platform: {payload.platform}\n"
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

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

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
            resp = await client.post(url, json=body)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"AI API error: {resp.text[:300]}")
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            fixed = parsed.get("fixed_steps")
            if not isinstance(fixed, list):
                raise HTTPException(status_code=502, detail="AI did not return fixed_steps[]")
            return {
                "analysis": parsed.get("analysis", ""),
                "fixed_steps": fixed,
                "changes": parsed.get("changes", []),
            }
    except HTTPException:
        raise
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
    test_name: str = ""
    screenshot_base64: str = ""
    acceptance_criteria: str = ""  # Source of truth
    app_context: str = ""  # App name, package from build metadata
    fix_history: list[dict[str, Any]] = []  # test's stored history from previous runs
    previous_analysis: str = ""
    previous_fixed_steps: list[dict[str, Any]] = []
    previous_changes: list[dict[str, Any]] = []
    user_suggestion: str = ""


@app.post("/api/ai/refine-fix")
async def refine_fix(payload: RefineFixRequest) -> dict[str, Any]:
    """Refine the AI fix based on user suggestion, with full context (original steps, step results, error, page source, screenshot, previous fix)."""
    s = _load_settings()
    api_key, model = _ai_creds(s)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    failed_step = payload.original_steps[payload.failed_step_index] if payload.failed_step_index < len(payload.original_steps) else {}

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
    )

    user_msg = f"Platform: {payload.platform}\nTest: {payload.test_name}\n"
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
        f"Error: {payload.error_message}\n\n"
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
        user_msg += f"=== PAGE SOURCE XML ===\n{payload.page_source_xml[:10000]}\n"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    parts: list[dict] = [{"text": f"{system_prompt}\n\n{user_msg}"}]
    if payload.screenshot_base64:
        raw = payload.screenshot_base64
        if "," in raw:
            raw = raw.split(",", 1)[1]
        parts.append({"inlineData": {"mimeType": "image/png", "data": raw}})

    body = {"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"AI API error: {resp.text[:300]}")
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            fixed = parsed.get("fixed_steps")
            if not isinstance(fixed, list):
                raise HTTPException(status_code=502, detail="AI did not return fixed_steps[]")
            return {
                "analysis": parsed.get("analysis", ""),
                "fixed_steps": fixed,
                "changes": parsed.get("changes", []),
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI refine fix failed: {e}")


# ── AI Edit Steps ─────────────────────────────────────────────────────

class EditStepsRequest(BaseModel):
    platform: str
    current_steps: list[dict[str, Any]]
    instruction: str


@app.post("/api/ai/edit-steps")
async def edit_steps(payload: EditStepsRequest) -> dict[str, Any]:
    s = _load_settings()
    api_key, model = _ai_creds(s)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI API key not configured. Set it in Settings.")

    system_prompt = (
        "You are a senior mobile QA engineer.\n"
        "The user has an existing set of Appium test steps and wants to modify them.\n"
        "Apply the user's instruction to the steps and return the updated full list.\n\n"
        "Return ONLY valid JSON:\n"
        '{"steps": [{"type": "tap|type|wait|waitForVisible|assertText|assertVisible|takeScreenshot|swipe",'
        ' "selector": {"using":"accessibilityId|id|xpath","value":"..."},'
        ' "text": "...", "ms": 1000, "expect":"..."}],'
        ' "summary": "Brief description of what was changed"}\n\n'
        "Return the COMPLETE step list (not just changed ones).\n"
        "No markdown, no explanation outside JSON."
    )
    user_msg = (
        f"Platform: {payload.platform}\n\n"
        f"=== CURRENT STEPS ===\n{json.dumps(payload.current_steps, indent=2)}\n\n"
        f"=== INSTRUCTION ===\n{payload.instruction}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_msg}"}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body)
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

@app.post("/api/appium/page-source")
async def get_page_source() -> dict[str, Any]:
    s = _load_settings()
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


# ── Triage ─────────────────────────────────────────────────────────────

@app.post("/api/runs/{run_id}/triage")
def triage_run(run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        if r.status not in ("failed", "error"):
            return {"classifications": [], "note": "Run is not failed/error"}
        failure = {
            "testCaseId": f"RUN-{r.id}",
            "testCaseName": f"Run {r.id} failure",
            "status": "FAILED",
            "stackTrace": r.error_message or "",
            "browser": r.platform,
        }
        test_results = {"testCases": [failure]}

    import sys
    repo_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo_root))
    from tools.bug_triage import classify_failures  # type: ignore
    classifications = classify_failures(test_results)
    return {"classifications": classifications}


# ── Projects ──────────────────────────────────────────────────────────

@app.post("/api/projects", response_model=ProjectOut)
def create_project(payload: ProjectCreate) -> ProjectOut:
    with SessionLocal() as db:
        existing = db.query(Project).filter(Project.name == payload.name).first()
        if existing:
            raise HTTPException(status_code=409, detail="Project name already exists")
        p = Project(name=payload.name)
        db.add(p)
        db.commit()
        db.refresh(p)
        return ProjectOut(id=p.id, name=p.name, created_at=p.created_at)


@app.get("/api/projects", response_model=list[ProjectOut])
def list_projects() -> list[ProjectOut]:
    with SessionLocal() as db:
        projects = db.query(Project).order_by(Project.created_at.desc()).all()
        return [ProjectOut(id=p.id, name=p.name, created_at=p.created_at) for p in projects]


# ── Modules ───────────────────────────────────────────────────────────

@app.post("/api/projects/{project_id}/modules", response_model=ModuleOut)
def create_module(project_id: int, payload: ModuleCreate) -> ModuleOut:
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        m = Module(project_id=project_id, name=payload.name)
        db.add(m)
        db.commit()
        db.refresh(m)
        return ModuleOut(id=m.id, project_id=m.project_id, name=m.name, created_at=m.created_at)


@app.get("/api/projects/{project_id}/modules", response_model=list[ModuleOut])
def list_modules(project_id: int) -> list[ModuleOut]:
    with SessionLocal() as db:
        mods = db.query(Module).filter(Module.project_id == project_id).order_by(Module.created_at).all()
        return [ModuleOut(id=m.id, project_id=m.project_id, name=m.name, created_at=m.created_at) for m in mods]


@app.put("/api/modules/{module_id}", response_model=ModuleOut)
def rename_module(module_id: int, payload: ModuleCreate) -> ModuleOut:
    with SessionLocal() as db:
        m = db.query(Module).filter(Module.id == module_id).first()
        if not m:
            raise HTTPException(status_code=404, detail="Module not found")
        m.name = payload.name
        db.commit()
        db.refresh(m)
        return ModuleOut(id=m.id, project_id=m.project_id, name=m.name, created_at=m.created_at)


@app.delete("/api/modules/{module_id}")
def delete_module(module_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        m = db.query(Module).filter(Module.id == module_id).first()
        if not m:
            raise HTTPException(status_code=404, detail="Module not found")
        db.delete(m)
        db.commit()
        return {"ok": True}


# ── Test Suites ───────────────────────────────────────────────────────

@app.post("/api/modules/{module_id}/suites", response_model=SuiteOut)
def create_suite(module_id: int, payload: SuiteCreate) -> SuiteOut:
    with SessionLocal() as db:
        m = db.query(Module).filter(Module.id == module_id).first()
        if not m:
            raise HTTPException(status_code=404, detail="Module not found")
        s = TestSuite(module_id=module_id, name=payload.name)
        db.add(s)
        db.commit()
        db.refresh(s)
        return SuiteOut(id=s.id, module_id=s.module_id, name=s.name, created_at=s.created_at)


@app.get("/api/modules/{module_id}/suites", response_model=list[SuiteOut])
def list_suites(module_id: int) -> list[SuiteOut]:
    with SessionLocal() as db:
        suites = db.query(TestSuite).filter(TestSuite.module_id == module_id).order_by(TestSuite.created_at).all()
        return [SuiteOut(id=s.id, module_id=s.module_id, name=s.name, created_at=s.created_at) for s in suites]


@app.put("/api/suites/{suite_id}", response_model=SuiteOut)
def rename_suite(suite_id: int, payload: SuiteCreate) -> SuiteOut:
    with SessionLocal() as db:
        s = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Suite not found")
        s.name = payload.name
        db.commit()
        db.refresh(s)
        return SuiteOut(id=s.id, module_id=s.module_id, name=s.name, created_at=s.created_at)


@app.delete("/api/suites/{suite_id}")
def delete_suite(suite_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        s = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Suite not found")
        db.delete(s)
        db.commit()
        return {"ok": True}


# ── APK Manifest Parsing ──────────────────────────────────────────────

def _find_aapt() -> str:
    candidates = [os.path.expanduser("~/Library/Android/sdk/build-tools")]
    for base in candidates:
        if os.path.exists(base):
            versions = sorted(os.listdir(base), reverse=True)
            for v in versions:
                aapt = os.path.join(base, v, "aapt")
                if os.path.exists(aapt):
                    return aapt
    return "aapt"

def _parse_apk_manifest(apk_path: str) -> dict:
    try:
        aapt = _find_aapt()
        result = subprocess.run([aapt, "dump", "badging", apk_path], capture_output=True, text=True, timeout=10)
        output = result.stdout
    except Exception:
        return {"file_name": Path(apk_path).name, "file_size_mb": round(os.path.getsize(apk_path) / 1024 / 1024, 1)}

    def extract(pattern, default=""):
        m = re.search(pattern, output)
        return m.group(1) if m else default

    return {
        "display_name":   extract(r"application-label:'([^']+)'"),
        "version_name":   extract(r"versionName='([^']+)'"),
        "version_code":   extract(r"versionCode='([^']+)'"),
        "package":        extract(r"package: name='([^']+)'"),
        "main_activity":  extract(r"launchable-activity: name='([^']+)'"),
        "min_sdk":        extract(r"sdkVersion:'([^']+)'"),
        "target_sdk":     extract(r"targetSdkVersion:'([^']+)'"),
        "file_name":      Path(apk_path).name,
        "file_size_mb":   round(os.path.getsize(apk_path) / 1024 / 1024, 1),
    }


# ── Builds ────────────────────────────────────────────────────────────

@app.post("/api/projects/{project_id}/builds", response_model=BuildOut)
async def upload_build(project_id: int, platform: str, file: UploadFile = File(...)) -> BuildOut:
    if platform not in ("android", "ios_sim"):
        raise HTTPException(status_code=400, detail="platform must be android|ios_sim")

    fname_lower = (file.filename or "").lower()
    if fname_lower.endswith((".app", ".app.zip", ".ipa")):
        platform = "ios_sim"
    elif fname_lower.endswith(".apk"):
        platform = "android"

    ensure_dirs()
    out_dir = settings.uploads_dir / str(project_id) / platform
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / file.filename

    content = await file.read()
    dest.write_bytes(content)

    meta: dict = {}
    if platform == "android" and str(dest).endswith(".apk"):
        meta = _parse_apk_manifest(str(dest))
    elif platform == "ios_sim":
        meta["bundle_id"] = ""
        meta["display_name"] = Path(file.filename or "app").stem

    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        b = Build(
            project_id=project_id,
            platform=platform,
            file_name=file.filename,
            file_path=str(dest),
            build_metadata=meta,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return BuildOut(
            id=b.id,
            project_id=b.project_id,
            platform=b.platform,
            file_name=b.file_name,
            created_at=b.created_at,
            metadata=b.build_metadata or {},
        )


@app.get("/api/projects/{project_id}/builds", response_model=list[BuildOut])
def list_builds(project_id: int) -> list[BuildOut]:
    with SessionLocal() as db:
        builds = db.query(Build).filter(Build.project_id == project_id).order_by(Build.created_at.desc()).all()
        return [
            BuildOut(
                id=b.id,
                project_id=b.project_id,
                platform=b.platform,
                file_name=b.file_name,
                created_at=b.created_at,
                metadata=b.build_metadata or {},
            )
            for b in builds
        ]


# ── Tests ─────────────────────────────────────────────────────────────

def _test_out(t: TestDefinition) -> TestOut:
    return TestOut(
        id=t.id,
        project_id=t.project_id,
        suite_id=t.suite_id,
        prerequisite_test_id=t.prerequisite_test_id,
        name=t.name,
        steps=t.steps,
        acceptance_criteria=getattr(t, "acceptance_criteria", None),
        fix_history=getattr(t, "fix_history", None) or [],
        created_at=t.created_at,
    )


@app.post("/api/projects/{project_id}/tests", response_model=TestOut)
def create_test(project_id: int, payload: TestCreate) -> TestOut:
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        t = TestDefinition(
            project_id=project_id,
            suite_id=payload.suite_id,
            prerequisite_test_id=payload.prerequisite_test_id,
            name=payload.name,
            steps=payload.steps,
            acceptance_criteria=payload.acceptance_criteria,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return _test_out(t)


@app.get("/api/projects/{project_id}/tests", response_model=list[TestOut])
def list_tests(project_id: int) -> list[TestOut]:
    with SessionLocal() as db:
        tests = db.query(TestDefinition).filter(TestDefinition.project_id == project_id).order_by(TestDefinition.created_at.desc()).all()
        return [_test_out(t) for t in tests]


@app.put("/api/tests/{test_id}", response_model=TestOut)
def update_test(test_id: int, payload: TestUpdate, request: Request) -> TestOut:
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        provided = payload.model_fields_set
        if "name" in provided and payload.name is not None:
            t.name = payload.name
        if "steps" in provided and payload.steps is not None:
            t.steps = payload.steps
        if "suite_id" in provided:
            t.suite_id = payload.suite_id
        if "prerequisite_test_id" in provided:
            t.prerequisite_test_id = payload.prerequisite_test_id
        if "acceptance_criteria" in provided:
            t.acceptance_criteria = payload.acceptance_criteria
        db.commit()
        db.refresh(t)
        return _test_out(t)


class AppendFixHistoryRequest(BaseModel):
    analysis: str = ""
    fixed_steps: list[dict[str, Any]]
    changes: list[dict[str, Any]] = []
    run_id: Optional[int] = None
    steps_before_fix: Optional[list[dict[str, Any]]] = None


@app.post("/api/tests/{test_id}/append-fix-history")
def append_fix_history(test_id: int, payload: AppendFixHistoryRequest) -> dict[str, Any]:
    """Append a fix to the test's history when user applies it. Keeps last 10 entries."""
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        history = list(getattr(t, "fix_history", None) or [])
        entry = {
            "analysis": payload.analysis[:500],
            "fixed_steps": payload.fixed_steps,
            "changes": payload.changes,
            "run_id": payload.run_id,
            "created_at": datetime.utcnow().isoformat(),
        }
        if payload.steps_before_fix is not None:
            entry["steps_before_fix"] = payload.steps_before_fix
        history.append(entry)
        t.fix_history = history[-10:]
        db.commit()
        return {"ok": True, "history_length": len(t.fix_history)}


@app.post("/api/tests/{test_id}/undo-last-fix")
def undo_last_fix(test_id: int) -> dict[str, Any]:
    """Revert test steps to before the last AI fix. Removes last fix_history entry."""
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        history = list(getattr(t, "fix_history", None) or [])
        if not history:
            raise HTTPException(status_code=400, detail="No fix history to undo")
        last = history[-1]
        steps_before = last.get("steps_before_fix")
        if steps_before is None:
            raise HTTPException(status_code=400, detail="Cannot undo: previous steps not stored")
        t.steps = steps_before
        t.fix_history = history[:-1]
        db.commit()
        return {"ok": True, "steps": t.steps}


@app.delete("/api/tests/{test_id}")
def delete_test(test_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        db.delete(t)
        db.commit()
        return {"ok": True}


def _steps_equal(a: dict, b: dict) -> bool:
    """Compare two step dicts for equality (type, selector, text, etc.)."""
    if a.get("type") != b.get("type"):
        return False
    if json.dumps(a.get("selector") or {}, sort_keys=True) != json.dumps(b.get("selector") or {}, sort_keys=True):
        return False
    if a.get("text") != b.get("text"):
        return False
    if a.get("expect") != b.get("expect"):
        return False
    return True


def _shared_prefix_length(steps_a: list[dict], steps_b: list[dict]) -> int:
    """Return number of leading steps that match between two step lists."""
    n = 0
    for i in range(min(len(steps_a), len(steps_b))):
        if _steps_equal(steps_a[i], steps_b[i]):
            n += 1
        else:
            break
    return n


@app.get("/api/tests/{test_id}/related")
def get_related_tests(test_id: int) -> dict[str, Any]:
    """Return tests related to this one: dependents (use as prerequisite) and similar (share step prefix)."""
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        project_id = t.project_id
        my_steps = list(t.steps or [])

        dependents = db.query(TestDefinition).filter(
            TestDefinition.project_id == project_id,
            TestDefinition.prerequisite_test_id == test_id,
        ).all()

        similar: list[dict] = []
        others = db.query(TestDefinition).filter(
            TestDefinition.project_id == project_id,
            TestDefinition.id != test_id,
        ).all()
        dep_ids = {d.id for d in dependents}
        for o in others:
            if o.id in dep_ids:
                continue
            other_steps = list(o.steps or [])
            prefix_len = _shared_prefix_length(my_steps, other_steps)
            if prefix_len >= 2:
                similar.append({"test": _test_out(o), "shared_prefix_length": prefix_len})

        return {
            "dependents": [_test_out(d) for d in dependents],
            "similar": similar,
        }


class ApplyFixToRelatedRequest(BaseModel):
    fixed_steps: list[dict[str, Any]]
    prefix_length: int
    original_steps: list[dict[str, Any]]  # steps before fix, for finding similar tests
    test_ids: list[int] = []


@app.post("/api/tests/{test_id}/apply-fix-to-related")
def apply_fix_to_related(test_id: int, payload: ApplyFixToRelatedRequest) -> dict[str, Any]:
    """Apply the same step fix to related tests that share the step prefix."""
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        project_id = t.project_id
        original = list(payload.original_steps or [])

        related = db.query(TestDefinition).filter(
            TestDefinition.project_id == project_id,
            TestDefinition.id != test_id,
            TestDefinition.prerequisite_test_id != test_id,
        ).all()

        updated: list[int] = []
        prefix_len = min(payload.prefix_length, len(original), len(payload.fixed_steps))
        target_ids = set(payload.test_ids) if payload.test_ids else None

        for o in related:
            if target_ids is not None and o.id not in target_ids:
                continue
            other_steps = list(o.steps or [])
            shared = _shared_prefix_length(original, other_steps)
            if shared < prefix_len:
                continue
            new_steps = list(payload.fixed_steps[:prefix_len]) + other_steps[prefix_len:]
            o.steps = new_steps
            updated.append(o.id)

        db.commit()
        return {"updated_test_ids": updated}


# ── Runs ──────────────────────────────────────────────────────────────

@app.post("/api/runs", response_model=RunOut)
async def create_run(payload: RunCreate) -> RunOut:
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == payload.project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        t = db.query(TestDefinition).filter(TestDefinition.id == payload.test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")

        b = None
        if payload.build_id is not None:
            b = db.query(Build).filter(Build.id == payload.build_id).first()
            if not b:
                raise HTTPException(status_code=404, detail="Build not found")

        r = Run(
            project_id=payload.project_id,
            build_id=payload.build_id,
            test_id=payload.test_id,
            status="queued",
            device_target=payload.device_target,
            platform=payload.platform,
            artifacts={},
            summary={},
        )
        db.add(r)
        db.commit()
        db.refresh(r)

        asyncio.create_task(event_bus.publish(RunEvent(run_id=r.id, type="queued", payload={"runId": r.id})))
        asyncio.create_task(run_engine.enqueue(r.id))

        return _run_to_out(r)


@app.get("/api/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int) -> RunOut:
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        return _run_to_out(r)


@app.get("/api/projects/{project_id}/runs", response_model=list[RunOut])
def list_runs(project_id: int) -> list[RunOut]:
    with SessionLocal() as db:
        runs = db.query(Run).filter(Run.project_id == project_id).order_by(Run.id.desc()).limit(100).all()
        return [_run_to_out(r) for r in runs]


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        db.delete(r)
        db.commit()
        return {"ok": True}


# ── Artifacts ─────────────────────────────────────────────────────────

@app.get("/api/artifacts/{project_id}/{run_id}/{name}")
def get_artifact(project_id: int, run_id: int, name: str) -> FileResponse:
    path = settings.artifacts_dir / str(project_id) / str(run_id) / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(str(path), headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


# ── Katalon Export (Studio 9.x compatible) ───────────────────────────

def _safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)

def guessModule(name: str) -> str:
    parts = re.split(r"[_\s\-]+", name)
    return parts[0].capitalize() if parts else "General"

@app.get("/api/runs/{run_id}/katalon")
def export_katalon(run_id: int):
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        t = db.query(TestDefinition).filter(TestDefinition.id == r.test_id).first() if r.test_id else None
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        p = db.query(Project).filter(Project.id == r.project_id).first()
        proj_name = _safe_name(p.name if p else "QA_Project")
        tc_name = _safe_name(t.name)
        module = guessModule(t.name) if hasattr(t, "name") else "General"
        module_safe = _safe_name(module)
        tc_path = f"Test Cases/Mobile/{module_safe}/{tc_name}"

        groovy_lines = [
            "import static com.kms.katalon.core.testobject.ObjectRepository.findTestObject",
            "import com.kms.katalon.core.mobile.keyword.MobileBuiltInKeywords as Mobile",
            "",
            f"// Test: {t.name}",
            f"// Generated from QA·OS Run #{r.id}",
            "",
        ]
        obj_files: dict[str, str] = {}

        for i, s in enumerate(t.steps or []):
            stype = s.get("type", "")
            sel = s.get("selector", {})
            using = sel.get("using", "accessibilityId")
            value = sel.get("value", "")
            text = s.get("text", "")
            ms = s.get("ms", 1000)
            obj_name = f"step_{i:03d}"
            obj_ref = f"'Object Repository/Screen_{module_safe}/{obj_name}'"

            if value:
                prop_map = {"accessibilityId": "accessibility id", "id": "resource-id", "xpath": "xpath", "className": "class"}
                prop = prop_map.get(using, "accessibility id")
                obj_files[f"Object Repository/Screen_{module_safe}/{obj_name}.rs"] = (
                    f'<?xml version="1.0" encoding="UTF-8"?>\n'
                    f"<WebElementEntity>\n"
                    f"  <description></description>\n"
                    f"  <name>{obj_name}</name>\n"
                    f"  <tag></tag>\n"
                    f"  <elementGuid>{uuid.uuid4()}</elementGuid>\n"
                    f"  <selectorMethod>BASIC</selectorMethod>\n"
                    f"  <useRalativeImagePath>false</useRalativeImagePath>\n"
                    f"  <webElementProperties>\n"
                    f"    <isSelected>true</isSelected>\n"
                    f"    <matchCondition>equals</matchCondition>\n"
                    f"    <name>{prop}</name>\n"
                    f"    <type>Main</type>\n"
                    f"    <value>{value}</value>\n"
                    f"  </webElementProperties>\n"
                    f"</WebElementEntity>"
                )

            if stype == "tap":
                groovy_lines.append(f"Mobile.tap(findTestObject({obj_ref}), 10)")
            elif stype == "type":
                groovy_lines.append(f"Mobile.tap(findTestObject({obj_ref}), 10)")
                groovy_lines.append(f"Mobile.setText(findTestObject({obj_ref}), '{text}', 10)")
            elif stype == "wait":
                groovy_lines.append(f"Mobile.delay({ms / 1000})")
            elif stype in ("waitForVisible", "assertVisible"):
                groovy_lines.append(f"Mobile.waitForElementPresent(findTestObject({obj_ref}), 10)")
            elif stype == "assertText":
                groovy_lines.append(f"Mobile.verifyElementText(findTestObject({obj_ref}), '{s.get('expect', '')}')")
            elif stype == "takeScreenshot":
                groovy_lines.append(f"Mobile.takeScreenshot('screenshots/step_{i:03d}.png')")
            elif stype == "swipe":
                groovy_lines.append(f"Mobile.swipe(100, 800, 100, 200)")
            else:
                groovy_lines.append(f"// Step {i}: {stype}")

        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # .project (Eclipse descriptor)
            zf.writestr(f"{proj_name}/.project",
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<projectDescription>\n  <name>{proj_name}</name>\n  <comment></comment>\n"
                f"  <projects></projects>\n  <buildSpec></buildSpec>\n  <natures>\n"
                f"    <nature>com.kms.katalon.core.katalon</nature>\n  </natures>\n</projectDescription>")

            # .prj (Katalon project file)
            zf.writestr(f"{proj_name}/{proj_name}.prj",
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<ProjectEntity>\n  <name>{proj_name}</name>\n  <description></description>\n"
                f"  <type>MOBILE</type>\n  <defaultProfile>default</defaultProfile>\n</ProjectEntity>")

            # settings/internal.properties
            zf.writestr(f"{proj_name}/settings/internal.properties",
                "com.kms.katalon.core.testcase.version=1\n")

            # Profiles/default.glbl
            zf.writestr(f"{proj_name}/Profiles/default.glbl",
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<GlobalVariableEntities>\n  <description></description>\n  <name>default</name>\n"
                "  <defaultProfile>true</defaultProfile>\n  <globalVariableEntities/>\n</GlobalVariableEntities>")

            # Test Case .groovy
            zf.writestr(f"{proj_name}/{tc_path}.groovy", "\n".join(groovy_lines) + "\n")

            # Test Case .tc companion
            zf.writestr(f"{proj_name}/{tc_path}.tc",
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<TestCaseEntity>\n  <description>Generated from QA·OS</description>\n"
                f"  <name>{tc_name}</name>\n  <tag></tag>\n  <comment>{t.name}</comment>\n"
                f"  <testCaseGuid>{uuid.uuid4()}</testCaseGuid>\n</TestCaseEntity>")

            # Object Repository .rs files
            for path, content in obj_files.items():
                zf.writestr(f"{proj_name}/{path}", content)

            # Test Suite .ts + .groovy
            zf.writestr(f"{proj_name}/Test Suites/smoke.groovy", "")
            zf.writestr(f"{proj_name}/Test Suites/smoke.ts",
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<TestSuiteEntity>\n  <description></description>\n  <name>smoke</name>\n"
                f"  <tag></tag>\n  <isRerun>false</isRerun>\n  <mailRecipient></mailRecipient>\n"
                f"  <numberOfRerun>0</numberOfRerun>\n  <pageLoadTimeout>30</pageLoadTimeout>\n"
                f"  <rerunFailedTestCasesOnly>false</rerunFailedTestCasesOnly>\n"
                f"  <rerunImmediately>false</rerunImmediately>\n"
                f"  <testSuiteGuid>{uuid.uuid4()}</testSuiteGuid>\n"
                f"  <testSuiteTestCaseLink>\n    <testCaseId>{tc_path}</testCaseId>\n"
                f"    <runEnabled>true</runEnabled>\n    <usingDataBinding>false</usingDataBinding>\n"
                f"  </testSuiteTestCaseLink>\n</TestSuiteEntity>")

            # Keywords directory placeholder
            zf.writestr(f"{proj_name}/Keywords/.gitkeep", "")

        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={proj_name}_katalon.zip"},
        )


# ── WebSocket ─────────────────────────────────────────────────────────

@app.websocket("/ws/runs/{run_id}")
async def ws_run_events(websocket: WebSocket, run_id: int):
    await websocket.accept()
    q = event_bus.subscribe(run_id)
    try:
        while True:
            event = await q.get()
            await websocket.send_text(json.dumps({"type": event.type, "payload": event.payload}))
    except WebSocketDisconnect:
        pass
    finally:
        event_bus.unsubscribe(run_id, q)
