from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import time
import uuid
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .compose_detection import is_compose_screen
from .swiftui_detection import is_swiftui_screen
from .db import SessionLocal, init_db, Project, Module, TestSuite, Build, TestDefinition, Run, BatchRun, ScreenLibrary, ScreenFolder, _classify_error
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
    BatchRunCreate,
    BatchRunOut,
    BatchRunChildOut,
)
from .settings import ensure_dirs, load_encrypted_json, save_encrypted_json, settings
from .parser.script_generator import generate_katalon_zip, safe_katalon_name, steps_to_groovy
from .parser.script_parser import (
    group_steps_into_test_cases,
    katalon_or_leaves_and_aliases,
    parse_groovy,
    parse_gherkin,
    parse_test_sheet,
    sheet_row_combined_steps,
)
from .parser.zip_importer import (
    ParsedFile, extract_folder_name, parse_folder_files, parse_zip,
    parse_object_repo_from_zip, parse_object_repo_from_files,
    parse_katalon_project, KatalonProjectStructure, _normalize_katalon_path, _tc_id_from_groovy_path,
)
from .runner.appium_service import ensure_appium_running
from .runner.engine import run_engine
from .runner.screen_capture_session import (
    evict_dead_session,
    get_slot_for_status,
    make_session_key,
    session_active_and_alive,
    set_session_driver,
    stop_session,
    with_session_driver,
)
from .runner.ai_fix_diagnosis import (
    AI_FIX_CLASSIFICATION_RULES,
    build_failure_diagnosis_block,
    classify_failure_for_ai_fix,
    parse_android_package,
)
from .runner.tap_debugger import diagnose_tap_failure
from pydantic import BaseModel


app = FastAPI(title="QA Platform (Local Appium TestOps)", version="0.1.0")

ALLOWED_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:5174", "http://localhost:5174"]
AUTH_COOKIE_NAME = "qa_os_token"
AUTH_TOKEN_FILE = settings.data_dir / "token.txt"

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SETTINGS_FILE = settings.data_dir / "settings.json"
ONBOARDING_FILE = settings.data_dir / "onboarding.json"

_figma_components_cache: dict[str, Any] = {"ts": 0.0, "names": []}


def _load_settings() -> dict:
    return load_encrypted_json(SETTINGS_FILE)


def _ai_creds(s: dict | None = None) -> tuple[str, str]:
    if s is None:
        s = _load_settings()
    key = s.get("ai_api_key") or s.get("ai_key") or ""
    model = s.get("ai_model") or "gemini-2.5-flash"
    return key, model


def _gemini_extract_text(data: dict[str, Any]) -> str:
    """Pull model text from generateContent JSON; raise HTTPException with a clear message if unusable."""
    err = data.get("error")
    if err:
        if isinstance(err, dict):
            msg = str(err.get("message", json.dumps(err)[:400]))
        else:
            msg = str(err)
        raise HTTPException(status_code=502, detail=f"Gemini API error: {msg}")
    cands = data.get("candidates")
    if not cands:
        fb = data.get("promptFeedback")
        extra = json.dumps(fb)[:500] if fb else "(none)"
        raise HTTPException(
            status_code=502,
            detail=(
                "Gemini returned no candidates — often quota, safety block, or wrong model id. "
                f"Check Settings → AI model name and API key. promptFeedback={extra}"
            ),
        )
    first = cands[0]
    content = first.get("content") or {}
    parts = content.get("parts") or []
    if not parts:
        reason = first.get("finishReason", "")
        raise HTTPException(
            status_code=502,
            detail=f"Gemini returned no text (finishReason={reason}). Try again or shorten screenshot/XML payload.",
        )
    text = parts[0].get("text")
    if not text:
        raise HTTPException(status_code=502, detail="Gemini returned an empty text part.")
    return text


def _save_settings(data: dict) -> None:
    save_encrypted_json(SETTINGS_FILE, data)


def _write_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _get_auth_token() -> str:
    ensure_dirs()
    if AUTH_TOKEN_FILE.exists():
        token = AUTH_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    _write_private_text(AUTH_TOKEN_FILE, f"{token}\n")
    return token


def _extract_bearer_token(value: str | None) -> str:
    if not value:
        return ""
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _extract_request_token(request: Request) -> str:
    return (
        _extract_bearer_token(request.headers.get("Authorization"))
        or request.cookies.get(AUTH_COOKIE_NAME, "")
        or request.query_params.get("token", "")
    )


def _extract_websocket_token(websocket: WebSocket) -> str:
    return (
        _extract_bearer_token(websocket.headers.get("authorization"))
        or websocket.cookies.get(AUTH_COOKIE_NAME, "")
        or websocket.query_params.get("token", "")
    )


@app.on_event("startup")
async def _startup() -> None:
    ensure_dirs()
    init_db()
    _get_auth_token()
    run_engine.start()


@app.middleware("http")
async def require_local_token(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in {"/api/health", "/api/auth/token"}:
        return await call_next(request)

    if _extract_request_token(request) != _get_auth_token():
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)


def _run_to_out(r: Run) -> RunOut:
    return RunOut(
        id=r.id,
        project_id=r.project_id,
        build_id=r.build_id,
        test_id=r.test_id,
        batch_run_id=getattr(r, "batch_run_id", None),
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


@app.get("/api/auth/token")
def get_auth_token(request: Request) -> JSONResponse:
    if request.headers.get("origin") not in (None, "", *ALLOWED_ORIGINS):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    token = _get_auth_token()
    response = JSONResponse({"token": token})
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
    )
    return response


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
    port = int(s.get("appium_port", settings.appium_port))
    url = f"http://{host}:{port}/status"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
            if r.status_code == 200:
                return {"ok": True, "message": f"Appium is running at {host}:{port}"}
            return {"ok": False, "message": f"Appium returned HTTP {r.status_code}"}
    except Exception as e:
        handle = ensure_appium_running(host=host, port=port)
        if handle is not None:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(url)
                    if r.status_code == 200:
                        return {"ok": True, "message": f"Started Appium at {host}:{port}"}
            except Exception:
                pass
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


@app.get("/api/integrations/figma/components")
def list_figma_components() -> dict[str, Any]:
    """Return component names from the configured Figma file (cached 5 minutes)."""
    s = _load_settings()
    token = (s.get("figma_token") or "").strip()
    file_key = (s.get("figma_file_key") or "").strip()
    if not token or not file_key:
        raise HTTPException(status_code=400, detail="Configure Figma token and file key in Settings")
    now = time.time()
    if now - float(_figma_components_cache["ts"]) < 300 and _figma_components_cache.get("names"):
        return {"names": _figma_components_cache["names"]}
    try:
        r = httpx.get(
            f"https://api.figma.com/v1/files/{file_key}/components",
            headers={"X-Figma-Token": token},
            timeout=30,
        )
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Figma API error: HTTP {r.status_code}")
        data = r.json()
        meta = data.get("meta") or {}
        components = meta.get("components") or {}
        names = sorted({str(v.get("name") or "") for v in components.values() if v.get("name")})
        _figma_components_cache["ts"] = now
        _figma_components_cache["names"] = names
        return {"names": names}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Figma request failed: {e}") from e


@app.post("/api/projects/{project_id}/confluence/sync")
async def sync_project_to_confluence(project_id: int) -> dict[str, Any]:
    """Create a Confluence page with a table of tests and latest run status."""
    import html as html_lib

    s = _load_settings()
    base = (s.get("confluence_url") or "").rstrip("/")
    token = (s.get("confluence_token") or "").strip()
    space_key = (s.get("confluence_space_key") or "").strip()
    if not base or not token:
        raise HTTPException(status_code=400, detail="Configure Confluence URL and API token in Settings")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}

    with SessionLocal() as db:
        proj = db.query(Project).filter(Project.id == project_id).first()
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        tests = db.query(TestDefinition).filter(TestDefinition.project_id == project_id).order_by(TestDefinition.name).all()
        runs = db.query(Run).filter(Run.project_id == project_id).order_by(Run.id.desc()).all()
        sid_list = list({t.suite_id for t in tests if t.suite_id})
        suite_map: dict[int, str] = {}
        if sid_list:
            for su in db.query(TestSuite).filter(TestSuite.id.in_(sid_list)).all():
                suite_map[su.id] = su.name

    latest_by_test: dict[int, Run] = {}
    for r in runs:
        tid = r.test_id
        if tid and tid not in latest_by_test:
            latest_by_test[tid] = r

    rows_html = []
    for t in tests:
        lr = latest_by_test.get(t.id)
        st = lr.status if lr else "not_run"
        plat = lr.platform if lr else "—"
        finished = lr.finished_at.isoformat() if lr and lr.finished_at else "—"
        suite = suite_map.get(t.suite_id, "") if t.suite_id else ""
        rows_html.append(
            "<tr>"
            f"<td>{html_lib.escape(t.name)}</td>"
            f"<td>{html_lib.escape(suite)}</td>"
            f"<td>{html_lib.escape(str(st))}</td>"
            f"<td>{html_lib.escape(str(plat))}</td>"
            f"<td>{html_lib.escape(finished)}</td>"
            "</tr>"
        )

    tbody = "".join(rows_html) if rows_html else '<tr><td colspan="5">No tests</td></tr>'
    table = (
        "<table>"
        "<thead><tr><th>Test</th><th>Suite</th><th>Latest run</th><th>Platform</th><th>Finished</th></tr></thead>"
        f"<tbody>{tbody}</tbody>"
        "</table>"
    )
    title = f"QA-OS — {proj.name} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
    body_html = (
        f"<p>Automated test status from <strong>QA-OS</strong> (project id {project_id}).</p>"
        f"{table}"
    )

    async with httpx.AsyncClient(timeout=45) as client:
        if not space_key:
            r = await client.get(f"{base}/rest/api/space?limit=10", headers=headers)
            if r.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Could not list Confluence spaces: HTTP {r.status_code}")
            results = r.json().get("results") or []
            if not results:
                raise HTTPException(
                    status_code=400,
                    detail="No spaces found — open Confluence and create a space, or set Confluence space key in Settings",
                )
            space_key = str(results[0].get("key") or "")

        payload = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": body_html, "representation": "storage"}},
        }
        r = await client.post(f"{base}/rest/api/content", json=payload, headers=headers)
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"Confluence create page failed: HTTP {r.status_code} {r.text[:400]}")
        data = r.json()
        links = data.get("_links") or {}
        web_ui = links.get("webui") or ""
        page_id = data.get("id") or ""
        base_ui = base.rstrip("/")
        page_url = f"{base_ui}{web_ui}" if web_ui else base_ui
        return {"ok": True, "page_id": page_id, "page_url": page_url, "space_key": space_key, "title": title}


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


def _filter_screen_library_by_build(
    q: Any,
    build_ids: Optional[list[int]] = None,
    build_id_legacy: Optional[int] = None,
) -> Any:
    """Restrict ScreenLibrary query: non-empty build_ids wins; else legacy singular build_id; else no filter."""
    ids = [b for b in (build_ids or []) if b is not None]
    if ids:
        return q.filter(ScreenLibrary.build_id.in_(ids))
    if build_id_legacy is not None:
        return q.filter(ScreenLibrary.build_id == build_id_legacy)
    return q


class GenerateStepsRequest(BaseModel):
    platform: str
    prompt: str
    page_source_xml: str = ""
    screen_names: list[str] = []
    folder_id: Optional[int] = None
    project_id: Optional[int] = None
    build_id: Optional[int] = None
    build_ids: Optional[list[int]] = None


@app.post("/api/ai/generate-steps")
async def generate_steps(payload: GenerateStepsRequest) -> dict[str, Any]:
    if payload.platform not in ("android", "ios_sim"):
        raise HTTPException(status_code=400, detail="platform must be android|ios_sim")

    s = _load_settings()
    api_key, model = _ai_creds(s)

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
            q = _filter_screen_library_by_build(q, payload.build_ids, payload.build_id)
            screens = q.all()
            if screens:
                screens_for_prompt = list(screens)
                xml_context = _build_xml_context(screens)
                grounded = True
                for scr in screens:
                    if scr.screenshot_path:
                        fpath = settings.artifacts_dir / str(scr.project_id) / scr.screenshot_path
                        if fpath.exists():
                            img_b64 = _compress_screenshot(fpath)
                            if img_b64:
                                screen_images.append((scr.name, img_b64))

    using_choices = (
        "accessibilityId|id|xpath|-android uiautomator"
        if payload.platform == "android"
        else "accessibilityId|id|xpath|-ios predicate string|-ios class chain"
    )

    if grounded and xml_context:
        android_rules = _android_selector_generation_rules(screens_for_prompt) if payload.platform == "android" else ""
        ios_rules = _ios_selector_generation_rules(screens_for_prompt) if payload.platform == "ios_sim" else ""
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

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
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
            resp = await client.post(url, json=body)
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


@app.post("/api/ai/generate-suite")
async def generate_suite(payload: GenerateSuiteRequest) -> dict[str, Any]:
    if payload.platform not in ("android", "ios_sim"):
        raise HTTPException(status_code=400, detail="platform must be android|ios_sim")

    s = _load_settings()
    api_key, model = _ai_creds(s)
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
            q = _filter_screen_library_by_build(q, payload.build_ids, None)
            screens = q.all()
            if screens:
                screens_for_prompt = list(screens)
                xml_context = _build_xml_context(screens)
                for scr in screens:
                    if scr.screenshot_path:
                        fpath = settings.artifacts_dir / str(scr.project_id) / scr.screenshot_path
                        if fpath.exists():
                            img_b64 = _compress_screenshot(fpath)
                            if img_b64:
                                screen_images.append((scr.name, img_b64))

    grounded = bool(xml_context)
    using_choices_suite = (
        "accessibilityId|id|xpath|-android uiautomator"
        if payload.platform == "android"
        else "accessibilityId|id|xpath|-ios predicate string|-ios class chain"
    )
    if grounded:
        android_rules = _android_selector_generation_rules(screens_for_prompt) if payload.platform == "android" else ""
        ios_rules = _ios_selector_generation_rules(screens_for_prompt) if payload.platform == "ios_sim" else ""
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

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
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


@app.post("/api/ai/fix-steps")
async def fix_steps(payload: FixStepsRequest) -> dict[str, Any]:
    s = _load_settings()
    api_key, model = _ai_creds(s)

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
            text = _gemini_extract_text(data)
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


@app.post("/api/ai/refine-fix")
async def refine_fix(payload: RefineFixRequest) -> dict[str, Any]:
    """Refine the AI fix based on user suggestion, with full context (original steps, step results, error, page source, screenshot, previous fix)."""
    s = _load_settings()
    api_key, model = _ai_creds(s)
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
            text = _gemini_extract_text(data)
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


# ── Screen Library ─────────────────────────────────────────────────────

def _compress_screenshot(fpath: Path, max_dim: int = 512) -> str:
    """Resize a screenshot to fit within max_dim and return base64 JPEG."""
    try:
        from PIL import Image
        import io, base64
        img = Image.open(fpath)
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _effective_screen_type(screen: ScreenLibrary) -> str:
    """Android: compose | native. iOS: swiftui | uikit."""
    st = getattr(screen, "screen_type", None)
    plat = (screen.platform or "").lower()
    if plat in ("ios_sim", "ios"):
        if st in ("swiftui", "uikit"):
            return st
        if st == "native":
            return "uikit"
        xml = screen.xml_snapshot or ""
        return "swiftui" if is_swiftui_screen(xml) else "uikit"
    if plat != "android":
        return "native"
    if st in ("compose", "native"):
        return st
    return "compose" if is_compose_screen(screen.xml_snapshot) else "native"


def _android_selector_generation_rules(screens: list[ScreenLibrary]) -> str:
    """Extra prompt text: per-screen Compose vs native Android rules."""
    android_screens = [s for s in screens if s.platform == "android"]
    if not android_screens:
        return ""
    lines = [
        "ANDROID — PER-SCREEN SELECTOR MODE (must match each screen block header in DOM CONTEXT):",
    ]
    for s in android_screens:
        st = _effective_screen_type(s)
        if st == "compose":
            lines.append(
                f'  • "{s.name}" [compose]: NEVER use selector.using "id". '
                'ALWAYS use "-android uiautomator" with UiSelector Java, e.g. '
                'new UiSelector().resourceId("com.package:id/element"), or .descriptionContains("..."), .textContains("...") '
                "when resource-id is absent."
            )
        else:
            lines.append(
                f'  • "{s.name}" [native]: prefer stable resource-id using Appium "id" strategy; '
                "then content-desc/accessibilityId, then text, then xpath as in the priority order."
            )
    return "\n".join(lines) + "\n\n"


def _ios_selector_generation_rules(screens: list[ScreenLibrary]) -> str:
    """Extra prompt text: per-screen SwiftUI vs UIKit iOS rules."""
    ios_screens = [s for s in screens if (s.platform or "").lower() in ("ios_sim", "ios")]
    if not ios_screens:
        return ""
    lines = [
        "iOS — PER-SCREEN SELECTOR MODE (must match each screen block header in DOM CONTEXT):",
    ]
    for s in ios_screens:
        st = _effective_screen_type(s)
        if st == "swiftui":
            lines.append(
                f'  • "{s.name}" [swiftui]: Prefer "-ios predicate string" with name (accessibility identifier) or label, '
                'e.g. {"using": "-ios predicate string", "value": "name == \'my_id\'"}. '
                "If multiple matches, use \"-ios class chain\" with **/XCUIElementType...[`name == '...'`]. "
                'Avoid bare "id" for taps; try "accessibility id" only when the XML shows a stable name= attribute.'
            )
        else:
            lines.append(
                '  • "' + s.name + '" [uikit]: Prefer {"using": "accessibility id", "value": "<name from XML>"} when name= is set; '
                'otherwise "-ios predicate string" on label or type; class chain if nested; xpath last resort.'
            )
    return "\n".join(lines) + "\n\n"


def _build_xml_context(screens: list[ScreenLibrary]) -> str:
    """Extract interactive elements from page source XML to reduce token usage."""
    import xml.etree.ElementTree as ET
    INTERACTIVE_CLASSES = {
        "android.widget.Button", "android.widget.EditText", "android.widget.CheckBox",
        "android.widget.RadioButton", "android.widget.Spinner", "android.widget.ImageButton",
        "android.view.View", "android.widget.TextView", "android.widget.ImageView",
        "android.widget.Switch", "android.widget.ToggleButton",
        "XCUIElementTypeButton", "XCUIElementTypeTextField", "XCUIElementTypeSecureTextField",
        "XCUIElementTypeStaticText", "XCUIElementTypeSwitch", "XCUIElementTypeImage",
    }
    chunks = []
    for screen in screens:
        try:
            root = ET.fromstring(screen.xml_snapshot)
        except ET.ParseError:
            chunks.append(f"=== {screen.name} ===\n[XML parse error]")
            continue
        elements = []
        for el in root.iter():
            cls = el.get("class", "") or el.get("type", "")
            clickable = el.get("clickable") == "true"
            focusable = el.get("focusable") == "true"
            rid = el.get("resource-id", "")
            cdesc = el.get("content-desc", "")
            txt = el.get("text", "")
            name_attr = el.get("name", "")
            label_attr = el.get("label", "")
            if not (cls in INTERACTIVE_CLASSES or clickable or focusable):
                continue
            if not (rid or cdesc or txt or name_attr or label_attr):
                continue
            parts = [f'class="{cls}"']
            if rid:
                parts.append(f'resource-id="{rid}"')
            if cdesc:
                parts.append(f'content-desc="{cdesc}"')
            if txt:
                parts.append(f'text="{txt}"')
            if name_attr:
                parts.append(f'name="{name_attr}"')
            if label_attr:
                parts.append(f'label="{label_attr}"')
            if clickable:
                parts.append('clickable="true"')
            elements.append("  " + " ".join(parts))
        if screen.platform == "android":
            st = _effective_screen_type(screen)
            header = f"=== {screen.name} (Android selector_strategy={st}) ==="
        elif (screen.platform or "").lower() in ("ios_sim", "ios"):
            st = _effective_screen_type(screen)
            header = f"=== {screen.name} (iOS selector_strategy={st}) ==="
        else:
            header = f"=== {screen.name} ==="
        chunks.append(header + "\n" + "\n".join(elements))
    return "\n\n".join(chunks)


def _screen_to_dict(s: ScreenLibrary, include_xml: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": s.id, "project_id": s.project_id, "build_id": s.build_id,
        "folder_id": s.folder_id,
        "name": s.name, "platform": s.platform,
        "screenshot_path": s.screenshot_path,
        "captured_at": s.captured_at.isoformat() if s.captured_at else None,
        "captured_by": s.captured_by, "notes": s.notes,
        "auto_captured": bool(s.auto_captured),
        "xml_length": len(s.xml_snapshot) if s.xml_snapshot else 0,
        "screen_type": getattr(s, "screen_type", None),
    }
    if include_xml:
        d["xml_snapshot"] = s.xml_snapshot
    return d


def _create_attach_driver(platform: str, device_target: str, server_url: str):
    """Create an Appium session that attaches to whatever is currently on
    screen — no app install, no relaunch.  Ideal for screen capture."""
    from appium.options.android import UiAutomator2Options
    from appium.options.ios import XCUITestOptions
    from appium import webdriver

    if platform == "android":
        opts = UiAutomator2Options()
        opts.platform_name = "Android"
        opts.automation_name = "UiAutomator2"
        if device_target:
            opts.udid = device_target
        opts.no_reset = True
        opts.auto_grant_permissions = True
        opts.new_command_timeout = 600
        opts.set_capability("appium:autoLaunch", False)
        opts.set_capability("appium:skipDeviceInitialization", True)
        opts.set_capability("appium:skipServerInstallation", True)
        # Try to avoid tearing down the foreground app when the capture session ends (driver.quit).
        opts.set_capability("appium:shouldTerminateApp", False)
        return webdriver.Remote(command_executor=server_url, options=opts)

    if platform in ("ios_sim", "ios"):
        opts = XCUITestOptions()
        opts.platform_name = "iOS"
        opts.automation_name = "XCUITest"
        if device_target:
            opts.udid = device_target
        opts.no_reset = True
        opts.auto_accept_alerts = True
        opts.new_command_timeout = 600
        return webdriver.Remote(command_executor=server_url, options=opts)

    raise ValueError(f"Unsupported platform: {platform}")


def _is_app_installed(device_target: str, package: str) -> bool:
    """Check if an Android package is already installed on the device."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["adb", "-s", device_target, "shell", "pm", "list", "packages", package],
            text=True, timeout=5,
        )
        return f"package:{package}" in out
    except Exception:
        return False


def _adb_uninstall(device_target: str, package: str) -> None:
    """Remove the package from the device (no-op if missing). Used before fresh reinstall."""
    if not device_target or not package:
        return
    subprocess.run(
        ["adb", "-s", device_target, "uninstall", package],
        timeout=120,
        capture_output=True,
    )


def _android_page_source_looks_like_launcher(xml: str) -> bool:
    """True if page source is clearly the system launcher (not an in-app screen)."""
    xl = (xml or "").lower()
    markers = (
        "com.android.launcher",
        "com.google.android.apps.nexuslauncher",
        "com.google.android.apps.launcher",
        "launcher3",
        "com.sec.android.app.launcher",
        "com.miui.home",
        "com.huawei.android.launcher",
    )
    return any(m in xl for m in markers)


def _bring_android_app_foreground(device_target: str, package: str, activity: str = "") -> None:
    """
    After Appium driver.quit(), sometimes the user lands on the home screen.
    Only used when the capture was of the launcher — never call this after an in-app capture,
    because `am start` relaunches the app and can advance onboarding / skip the first screen.
    """
    if not device_target or not package:
        return
    act = (activity or "").strip()
    if act:
        if act.startswith("."):
            comp = f"{package}/{act}"
        elif "/" in act:
            comp = act
        else:
            comp = f"{package}/{act}"
        subprocess.run(
            ["adb", "-s", device_target, "shell", "am", "start", "-n", comp],
            timeout=20,
            capture_output=True,
        )
        return
    subprocess.run(
        [
            "adb", "-s", device_target, "shell", "am", "start",
            "-a", "android.intent.action.MAIN",
            "-c", "android.intent.category.LAUNCHER",
            "-p", package,
        ],
        timeout=20,
        capture_output=True,
    )


def _ios_sim_uninstall(udid: str, bundle_id: str) -> None:
    """Remove app from booted simulator before fresh reinstall."""
    if not udid or not bundle_id:
        return
    xcrun = "/usr/bin/xcrun" if os.path.exists("/usr/bin/xcrun") else "xcrun"
    subprocess.run(
        [xcrun, "simctl", "uninstall", udid, bundle_id],
        timeout=120,
        capture_output=True,
    )


def _adb_devices_online() -> list[str]:
    """Serials of devices in `adb devices` state 'device'."""
    import subprocess

    try:
        adb_out = subprocess.check_output(["adb", "devices"], text=True, timeout=5)
    except Exception:
        return []
    out: list[str] = []
    for line in adb_out.strip().split("\n")[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append(parts[0])
    return out


def _resolve_android_device(requested: str | None) -> str:
    """Use requested serial if online; otherwise first device (legacy behavior)."""
    online = _adb_devices_online()
    if not online:
        return ""
    r = (requested or "").strip()
    if r and r in online:
        return r
    if r:
        for d in online:
            if r == d or d.endswith(r) or r in d:
                return d
    return online[0]


def _packages_from_other_builds_in_folder(folder_id: int, current_build_id: int) -> list[str]:
    """Android package names from screens in this folder saved under a different build."""
    out: set[str] = set()
    with SessionLocal() as db:
        for s in db.query(ScreenLibrary).filter(ScreenLibrary.folder_id == folder_id).all():
            if s.build_id is None or s.build_id == current_build_id:
                continue
            b = db.query(Build).filter(Build.id == s.build_id).first()
            if b and b.build_metadata:
                p = (b.build_metadata or {}).get("package") or ""
                if p.strip():
                    out.add(p.strip())
    return list(out)


def _bundle_ids_from_other_builds_in_folder(folder_id: int, current_build_id: int) -> list[str]:
    """iOS bundle IDs from screens in this folder saved under a different build."""
    out: set[str] = set()
    with SessionLocal() as db:
        for s in db.query(ScreenLibrary).filter(ScreenLibrary.folder_id == folder_id).all():
            if s.build_id is None or s.build_id == current_build_id:
                continue
            b = db.query(Build).filter(Build.id == s.build_id).first()
            if b and b.build_metadata:
                bid = (b.build_metadata or {}).get("bundle_id") or ""
                if bid.strip():
                    out.add(bid.strip())
    return list(out)


def _screen_folder_build_flags(folder_id: int, build_id: int) -> tuple[bool, bool]:
    """first_capture_in_folder, build_switch_reinstall (same rules as legacy capture)."""
    with SessionLocal() as db:
        n_folder = db.query(ScreenLibrary).filter(ScreenLibrary.folder_id == folder_id).count()
        first_capture_in_folder = n_folder == 0
        build_switch_reinstall = False
        if not first_capture_in_folder:
            rows = db.query(ScreenLibrary).filter(ScreenLibrary.folder_id == folder_id).all()
            has_this_build = any(s.build_id == build_id for s in rows)
            has_other_or_legacy = any(s.build_id is None or s.build_id != build_id for s in rows)
            build_switch_reinstall = (not has_this_build) and has_other_or_legacy
    return first_capture_in_folder, build_switch_reinstall


async def _screen_session_resolve_target(
    build_id: int,
    platform_hint: str,
    requested_device: str,
    *,
    ensure_appium_svc: bool = True,
) -> tuple[str, str, dict[str, Any], Optional[str]]:
    """Resolve platform, device, build metadata, and app path for screen session keying."""
    app_path: Optional[str] = None
    build_meta: dict[str, Any] = {}
    platform_val = platform_hint
    with SessionLocal() as db:
        b = db.query(Build).filter(Build.id == build_id).first()
        if not b:
            raise HTTPException(status_code=404, detail="Build not found")
        app_path = b.file_path
        build_meta = b.build_metadata or {}
        platform_val = b.platform or platform_val

    if ensure_appium_svc:
        await asyncio.get_event_loop().run_in_executor(None, ensure_appium_running)

    if platform_val == "android":
        device_target = await asyncio.get_event_loop().run_in_executor(
            None, _resolve_android_device, requested_device or None
        )
    elif platform_val in ("ios_sim", "ios"):
        device_target = (requested_device or "").strip()
    else:
        device_target = ""

    if not device_target and platform_val == "android":
        raise HTTPException(
            status_code=400,
            detail="No Android device or emulator found. Connect a device or start an emulator first.",
        )

    return platform_val, device_target, build_meta, app_path


async def _establish_screen_capture_driver(
    *,
    folder_id: int,
    build_id: int,
    platform_val: str,
    device_target: str,
    build_meta: dict[str, Any],
    app_path: Optional[str],
    first_capture_in_folder: bool,
    build_switch_reinstall: bool,
) -> tuple[Any, dict[str, bool], bool]:
    """Create Appium driver for screen capture start. Returns (driver, flags, used_install_path)."""
    from .runner.session import SessionConfig, create_driver

    s = _load_settings()
    host = s.get("appium_host", settings.appium_host)
    port = s.get("appium_port", settings.appium_port)
    base = f"http://{host}:{port}"

    needs_install = False
    fresh_install_for_folder = False
    build_changed_reinstall = False
    pkg = build_meta.get("package", "")
    bundle_id = (build_meta.get("bundle_id") or "").strip()

    if platform_val == "android" and pkg:
        if first_capture_in_folder and app_path:
            await asyncio.get_event_loop().run_in_executor(None, _adb_uninstall, device_target, pkg)
            needs_install = True
            fresh_install_for_folder = True
        elif build_switch_reinstall and app_path:
            for op in set(_packages_from_other_builds_in_folder(folder_id, build_id)):
                await asyncio.get_event_loop().run_in_executor(None, _adb_uninstall, device_target, op)
            await asyncio.get_event_loop().run_in_executor(None, _adb_uninstall, device_target, pkg)
            needs_install = True
            build_changed_reinstall = True
        else:
            needs_install = not await asyncio.get_event_loop().run_in_executor(
                None, _is_app_installed, device_target, pkg
            )

    elif platform_val in ("ios_sim", "ios") and bundle_id and app_path:
        if first_capture_in_folder and device_target:
            await asyncio.get_event_loop().run_in_executor(
                None, _ios_sim_uninstall, device_target, bundle_id
            )
            needs_install = True
            fresh_install_for_folder = True
        elif build_switch_reinstall and device_target:
            for bid in set(_bundle_ids_from_other_builds_in_folder(folder_id, build_id)):
                await asyncio.get_event_loop().run_in_executor(
                    None, _ios_sim_uninstall, device_target, bid
                )
            await asyncio.get_event_loop().run_in_executor(
                None, _ios_sim_uninstall, device_target, bundle_id
            )
            needs_install = True
            build_changed_reinstall = True

    used_install_path = bool(needs_install and app_path)
    if used_install_path:
        cfg = SessionConfig(
            platform=platform_val,
            device_target=device_target,
            app_path=app_path,
            build_meta=build_meta,
        )
        try:
            driver = await asyncio.get_event_loop().run_in_executor(None, create_driver, cfg)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to install & launch app: {e}")
        await asyncio.sleep(3)
    else:
        try:
            driver = await asyncio.get_event_loop().run_in_executor(
                None, _create_attach_driver, platform_val, device_target, base
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to create Appium session: {e}")

    flags = {
        "fresh_install": fresh_install_for_folder,
        "build_changed": build_changed_reinstall,
    }
    return driver, flags, used_install_path


@app.post("/api/screens/session/start")
async def start_screen_session(body: dict[str, Any]) -> dict[str, Any]:
    project_id = body.get("project_id")
    build_id = body.get("build_id")
    folder_id = body.get("folder_id")
    platform_hint = body.get("platform", "android")
    requested_device = (body.get("device_target") or "").strip()

    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    if not build_id:
        raise HTTPException(
            status_code=400,
            detail="build_id is required — pick a specific build (not Latest) to start a screen capture session.",
        )
    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required — select or create a folder first.")

    s = _load_settings()
    base = f"http://{s.get('appium_host', settings.appium_host)}:{s.get('appium_port', settings.appium_port)}"

    try:
        platform_val, device_target, build_meta, app_path = await _screen_session_resolve_target(
            int(build_id), platform_hint, requested_device, ensure_appium_svc=True
        )
        key = make_session_key(int(project_id), platform_val, device_target, int(build_id))

        if session_active_and_alive(key):
            return {"ok": True, "started": True, "reused": True, "flags": {}}

        evict_dead_session(key)

        first_capture_in_folder, build_switch_reinstall = _screen_folder_build_flags(int(folder_id), int(build_id))

        driver, flags, used_install_path = await _establish_screen_capture_driver(
            folder_id=int(folder_id),
            build_id=int(build_id),
            platform_val=platform_val,
            device_target=device_target,
            build_meta=build_meta,
            app_path=app_path,
            first_capture_in_folder=first_capture_in_folder,
            build_switch_reinstall=build_switch_reinstall,
        )

        await asyncio.sleep(0.35 if used_install_path else 0.9)
        set_session_driver(key, driver)
        return {"ok": True, "started": True, "reused": False, "flags": flags}
    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot connect to Appium server at {base}. Make sure Appium is running and a device is connected.",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=502,
            detail=f"Appium server at {base} timed out. The device may be unresponsive.",
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        raise HTTPException(status_code=502, detail=f"Appium communication failed: {err}")


@app.post("/api/screens/session/stop")
async def stop_screen_session(body: dict[str, Any]) -> dict[str, Any]:
    project_id = body.get("project_id")
    build_id = body.get("build_id")
    platform_hint = body.get("platform", "android")
    requested_device = (body.get("device_target") or "").strip()

    if not project_id or not build_id:
        raise HTTPException(status_code=400, detail="project_id and build_id are required")

    platform_val, device_target, _, _ = await _screen_session_resolve_target(
        int(build_id), platform_hint, requested_device, ensure_appium_svc=False
    )
    key = make_session_key(int(project_id), platform_val, device_target, int(build_id))
    existed = stop_session(key)
    return {"ok": True, "stopped": existed}


@app.get("/api/screens/session/status")
async def screen_session_status(
    project_id: int,
    build_id: int,
    platform: str = "android",
    device_target: str = "",
) -> dict[str, Any]:
    platform_val, resolved_device, _, _ = await _screen_session_resolve_target(
        build_id, platform, device_target, ensure_appium_svc=False
    )
    key = make_session_key(project_id, platform_val, resolved_device, build_id)
    slot = get_slot_for_status(key)
    alive = session_active_and_alive(key)
    out: dict[str, Any] = {"active": alive}
    if slot:
        if slot.get("created_at"):
            out["created_at"] = slot["created_at"].isoformat() + "Z"
        if slot.get("last_used"):
            out["last_used"] = slot["last_used"].isoformat() + "Z"
    return out


@app.post("/api/screens/capture")
async def capture_screen(body: dict[str, Any]) -> dict[str, Any]:
    project_id = body.get("project_id")
    build_id = body.get("build_id")
    folder_id = body.get("folder_id")
    name = (body.get("name") or "").strip()
    platform_hint = body.get("platform", "android")
    notes = body.get("notes", "")
    requested_device = (body.get("device_target") or "").strip()

    if not project_id or not name:
        raise HTTPException(status_code=400, detail="project_id and name are required")
    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id is required — select or create a folder first")
    if not build_id:
        raise HTTPException(
            status_code=400,
            detail="build_id is required — pick a specific build and run Start build before capturing.",
        )

    s = _load_settings()
    base = f"http://{s.get('appium_host', settings.appium_host)}:{s.get('appium_port', settings.appium_port)}"

    xml = ""
    shot_b64 = ""

    try:
        await asyncio.get_event_loop().run_in_executor(None, ensure_appium_running)

        platform_val, device_target, _, _ = await _screen_session_resolve_target(
            int(build_id), platform_hint, requested_device, ensure_appium_svc=False
        )
        key = make_session_key(int(project_id), platform_val, device_target, int(build_id))

        evict_dead_session(key)
        if not session_active_and_alive(key):
            raise HTTPException(
                status_code=409,
                detail="No active screen capture session for this device and build. Tap Start build first, or Start again if the session expired.",
            )

        await asyncio.sleep(0.25)

        def _grab(driver: Any) -> tuple[str, str]:
            return driver.page_source, driver.get_screenshot_as_base64()

        try:
            xml, shot_b64 = await asyncio.get_event_loop().run_in_executor(
                None, lambda: with_session_driver(key, _grab)
            )
        except RuntimeError:
            raise HTTPException(
                status_code=409,
                detail="Session expired or disconnected — tap Start build again.",
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to capture screen: {e}")

    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot connect to Appium server at {base}. Make sure Appium is running and a device is connected.",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=502,
            detail=f"Appium server at {base} timed out. The device may be unresponsive.",
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        raise HTTPException(status_code=502, detail=f"Appium communication failed: {err}")

    if not xml:
        raise HTTPException(status_code=502, detail="Appium returned empty page source")

    if platform_val == "android":
        screen_type_val = "compose" if is_compose_screen(xml) else "native"
    elif (platform_val or "").lower() in ("ios_sim", "ios"):
        screen_type_val = "swiftui" if is_swiftui_screen(xml) else "uikit"
    else:
        screen_type_val = "native"

    screenshot_path_val = None
    if shot_b64:
        import base64

        screen_dir = settings.artifacts_dir / str(project_id) / "screens"
        screen_dir.mkdir(parents=True, exist_ok=True)
        safe_name = name.replace(" ", "_").replace("/", "_")[:80]
        fname = f"{safe_name}_{platform_val}_{int(datetime.utcnow().timestamp())}.png"
        fpath = screen_dir / fname
        fpath.write_bytes(base64.b64decode(shot_b64))
        screenshot_path_val = f"screens/{fname}"

    with SessionLocal() as db:
        existing = db.query(ScreenLibrary).filter(
            ScreenLibrary.project_id == project_id,
            ScreenLibrary.build_id == build_id,
            ScreenLibrary.name == name,
            ScreenLibrary.platform == platform_val,
        ).first()
        if existing:
            existing.xml_snapshot = xml
            existing.screenshot_path = screenshot_path_val or existing.screenshot_path
            existing.captured_at = datetime.utcnow()
            existing.notes = notes or existing.notes
            existing.folder_id = folder_id or existing.folder_id
            existing.screen_type = screen_type_val
            db.commit()
            db.refresh(existing)
            return _screen_to_dict(existing)

        entry = ScreenLibrary(
            project_id=project_id,
            build_id=build_id,
            name=name,
            folder_id=folder_id,
            platform=platform_val,
            xml_snapshot=xml,
            screenshot_path=screenshot_path_val,
            notes=notes,
            screen_type=screen_type_val,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return _screen_to_dict(entry)


@app.get("/api/screen-folders")
def list_screen_folders(project_id: int) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        folders = db.query(ScreenFolder).filter(ScreenFolder.project_id == project_id).order_by(ScreenFolder.name).all()
        return [{"id": f.id, "project_id": f.project_id, "name": f.name,
                 "screen_count": db.query(ScreenLibrary).filter(ScreenLibrary.folder_id == f.id).count(),
                 "created_at": f.created_at.isoformat() if f.created_at else None} for f in folders]


@app.post("/api/screen-folders")
def create_screen_folder(body: dict[str, Any]) -> dict[str, Any]:
    project_id = body.get("project_id")
    name = (body.get("name") or "").strip()
    if not project_id or not name:
        raise HTTPException(status_code=400, detail="project_id and name are required")
    with SessionLocal() as db:
        existing = db.query(ScreenFolder).filter(ScreenFolder.project_id == project_id, ScreenFolder.name == name).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Folder '{name}' already exists")
        f = ScreenFolder(project_id=project_id, name=name)
        db.add(f)
        db.commit()
        db.refresh(f)
        return {"id": f.id, "project_id": f.project_id, "name": f.name, "screen_count": 0,
                "created_at": f.created_at.isoformat() if f.created_at else None}


@app.delete("/api/screen-folders/{folder_id}")
def delete_screen_folder(folder_id: int):
    with SessionLocal() as db:
        f = db.query(ScreenFolder).filter(ScreenFolder.id == folder_id).first()
        if not f:
            raise HTTPException(status_code=404, detail="Folder not found")
        db.delete(f)
        db.commit()
    return {"ok": True}


@app.get("/api/screens")
def list_screens(project_id: int, build_id: Optional[int] = None, folder_id: Optional[int] = None, platform: str = "") -> list[dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(ScreenLibrary).filter(ScreenLibrary.project_id == project_id)
        if build_id is not None:
            q = q.filter(ScreenLibrary.build_id == build_id)
        if folder_id is not None:
            q = q.filter(ScreenLibrary.folder_id == folder_id)
        if platform:
            q = q.filter(ScreenLibrary.platform == platform)
        screens = q.order_by(ScreenLibrary.captured_at.desc()).all()
        latest_build = db.query(Build).filter(Build.project_id == project_id).order_by(Build.id.desc()).first()
        latest_bid = latest_build.id if latest_build else None
        result = []
        for s in screens:
            d = _screen_to_dict(s)
            d["stale"] = s.build_id is not None and latest_bid is not None and s.build_id != latest_bid
            result.append(d)
        return result


@app.get("/api/screens/{screen_id}")
def get_screen(screen_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        s = db.query(ScreenLibrary).filter(ScreenLibrary.id == screen_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Screen not found")
        return _screen_to_dict(s, include_xml=True)


@app.put("/api/screens/{screen_id}")
def update_screen(screen_id: int, body: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        s = db.query(ScreenLibrary).filter(ScreenLibrary.id == screen_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Screen not found")
        if "name" in body:
            s.name = body["name"]
        if "notes" in body:
            s.notes = body["notes"]
        if "folder_id" in body:
            s.folder_id = body["folder_id"]
        db.commit()
        db.refresh(s)
        return _screen_to_dict(s)


@app.delete("/api/screens/{screen_id}")
def delete_screen(screen_id: int):
    with SessionLocal() as db:
        s = db.query(ScreenLibrary).filter(ScreenLibrary.id == screen_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Screen not found")
        db.delete(s)
        db.commit()
    return {"ok": True}


@app.get("/api/screens/{screen_id}/screenshot")
def screen_screenshot(screen_id: int):
    with SessionLocal() as db:
        s = db.query(ScreenLibrary).filter(ScreenLibrary.id == screen_id).first()
        if not s or not s.screenshot_path:
            raise HTTPException(status_code=404, detail="Screenshot not found")
        fpath = settings.artifacts_dir / str(s.project_id) / s.screenshot_path
        if not fpath.exists():
            raise HTTPException(status_code=404, detail="Screenshot file missing")
        return FileResponse(
            fpath,
            media_type="image/png",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
        )


# ── Triage ─────────────────────────────────────────────────────────────

def _classify_failure_message(msg: str, platform: str | None) -> dict[str, Any]:
    s = (msg or "").lower()
    category = "other"
    if any(x in s for x in ("nosuchelement", "no such element", "unable to locate", "could not find", "not found")):
        category = "selector_not_found"
    elif any(x in s for x in ("timeout", "timed out", "wait")):
        category = "element_timeout"
    elif "assertion" in s or "expected" in s or "assert" in s:
        category = "assertion_failure"
    elif any(x in s for x in ("connection", "network", "unreachable", "econnrefused", "socket")):
        category = "network_error"
    elif any(x in s for x in ("crash", "anr", "instrumentation")):
        category = "app_crash"
    return {
        "category": category,
        "platform": platform or "",
        "summary": (msg or "")[:500],
    }


@app.post("/api/runs/{run_id}/triage")
def triage_run(run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        if r.status not in ("failed", "error"):
            return {"classifications": [], "note": "Run is not failed/error"}
        stack = r.error_message or ""
        summary = (r.summary or {}) if isinstance(r.summary, dict) else {}
        step_results = summary.get("stepResults") or []
        failed_step = next((x for x in step_results if isinstance(x, dict) and x.get("status") == "failed"), None)
        if failed_step and isinstance(failed_step.get("details"), dict):
            err = failed_step["details"].get("error")
            if err:
                stack = str(err)
        c = _classify_failure_message(stack, r.platform)
        return {
            "classifications": [
                {
                    "testCaseId": f"RUN-{r.id}",
                    "category": c["category"],
                    "summary": c["summary"],
                    "platform": c["platform"],
                }
            ]
        }


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

_BUILD_ALLOWED_EXTENSIONS = {".apk", ".ipa", ".app", ".zip"}
_BUILD_MAX_SIZE_BYTES = 500 * 1024 * 1024  # 500MB


def _sanitize_build_filename(name: str) -> str:
    """Sanitize filename: strip path, remove dangerous chars."""
    p = Path(name or "build")
    safe = p.name
    safe = re.sub(r"[^\w\-\.]", "_", safe)
    return safe or "build"


@app.post("/api/projects/{project_id}/builds", response_model=BuildOut)
async def upload_build(project_id: int, platform: str, file: UploadFile = File(...)) -> BuildOut:
    if platform not in ("android", "ios_sim"):
        raise HTTPException(status_code=400, detail="platform must be android|ios_sim")

    fname_lower = (file.filename or "").lower()
    ext_ok = any(fname_lower.endswith(ext) for ext in _BUILD_ALLOWED_EXTENSIONS) or fname_lower.endswith(".app.zip")
    if not ext_ok:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(sorted(_BUILD_ALLOWED_EXTENSIONS))}, .app.zip",
        )

    if fname_lower.endswith((".app", ".app.zip", ".ipa")):
        platform = "ios_sim"
    elif fname_lower.endswith(".apk"):
        platform = "android"

    content = b""
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > _BUILD_MAX_SIZE_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds 500MB limit")
        content += chunk

    safe_fname = _sanitize_build_filename(file.filename)
    ensure_dirs()
    out_dir = settings.uploads_dir / str(project_id) / platform
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / safe_fname

    dest.write_bytes(content)

    meta: dict = {}
    if platform == "android" and str(dest).endswith(".apk"):
        meta = _parse_apk_manifest(str(dest))
    elif platform == "ios_sim":
        meta["bundle_id"] = ""
        meta["display_name"] = Path(safe_fname).stem

    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        b = Build(
            project_id=project_id,
            platform=platform,
            file_name=safe_fname,
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


@app.delete("/api/builds/{build_id}")
def delete_build(build_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        b = db.query(Build).filter(Build.id == build_id).first()
        if not b:
            raise HTTPException(status_code=404, detail="Build not found")
        file_path = Path(b.file_path) if b.file_path else None
        db.delete(b)
        db.commit()
    if file_path and file_path.exists():
        try:
            file_path.unlink()
        except OSError:
            pass
    return {"ok": True}


# ── Tests ─────────────────────────────────────────────────────────────

def _steps_for_platform_record(t: TestDefinition, platform: str) -> list[dict]:
    ps = getattr(t, "platform_steps", None) or {}
    if isinstance(ps, dict) and platform in ps and ps[platform]:
        return list(ps[platform])
    return list(t.steps or [])


def _test_out(t: TestDefinition) -> TestOut:
    ps = getattr(t, "platform_steps", None) or {}
    if not isinstance(ps, dict):
        ps = {}
    android_steps = list(ps.get("android") or t.steps or [])
    ios_steps = list(ps.get("ios_sim") or [])
    return TestOut(
        id=t.id,
        project_id=t.project_id,
        suite_id=t.suite_id,
        prerequisite_test_id=t.prerequisite_test_id,
        name=t.name,
        steps=android_steps,
        platform_steps={"android": android_steps, "ios_sim": ios_steps},
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
        ps: dict = dict(payload.platform_steps or {})
        legacy = list(payload.steps or [])
        if legacy and not ps.get("android"):
            ps["android"] = legacy
        if "android" not in ps:
            ps["android"] = []
        if "ios_sim" not in ps:
            ps["ios_sim"] = []
        android = list(ps.get("android") or [])
        t = TestDefinition(
            project_id=project_id,
            suite_id=payload.suite_id,
            prerequisite_test_id=payload.prerequisite_test_id,
            name=payload.name,
            steps=android,
            platform_steps=ps,
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
        if "suite_id" in provided:
            t.suite_id = payload.suite_id
        if "prerequisite_test_id" in provided:
            t.prerequisite_test_id = payload.prerequisite_test_id
        if "acceptance_criteria" in provided:
            t.acceptance_criteria = payload.acceptance_criteria

        if "platform_steps" in provided and payload.platform_steps is not None:
            current_ps = dict(t.platform_steps or {})
            for k, v in payload.platform_steps.items():
                if isinstance(v, list):
                    current_ps[str(k)] = v
            t.platform_steps = current_ps
            if "android" in current_ps:
                t.steps = list(current_ps["android"])
        elif "steps" in provided and payload.steps is not None:
            target_pf = payload.platform or "android"
            if target_pf not in ("android", "ios_sim"):
                target_pf = "android"
            current_ps = dict(t.platform_steps or {})
            current_ps[target_pf] = list(payload.steps)
            t.platform_steps = current_ps
            if target_pf == "android":
                t.steps = list(payload.steps)

        db.commit()
        db.refresh(t)
        return _test_out(t)


class AppendFixHistoryRequest(BaseModel):
    analysis: str = ""
    fixed_steps: list[dict[str, Any]]
    changes: list[dict[str, Any]] = []
    run_id: Optional[int] = None
    steps_before_fix: Optional[list[dict[str, Any]]] = None
    target_platform: str = "android"


@app.post("/api/tests/{test_id}/append-fix-history")
def append_fix_history(test_id: int, payload: AppendFixHistoryRequest) -> dict[str, Any]:
    """Append a fix to the test's history when user applies it. Keeps last 10 entries."""
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        history = list(getattr(t, "fix_history", None) or [])
        tp = payload.target_platform if payload.target_platform in ("android", "ios_sim") else "android"
        entry = {
            "analysis": payload.analysis,
            "fixed_steps": payload.fixed_steps,
            "changes": payload.changes,
            "run_id": payload.run_id,
            "target_platform": tp,
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
    """Revert steps for the platform recorded on the last fix. Removes last fix_history entry."""
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        history = list(getattr(t, "fix_history", None) or [])
        if not history:
            raise HTTPException(status_code=400, detail="No fix history to undo")
        last = history[-1]
        target_pf = last.get("target_platform") or "android"
        if target_pf not in ("android", "ios_sim"):
            target_pf = "android"
        steps_before = last.get("steps_before_fix")
        if steps_before is None:
            raise HTTPException(status_code=400, detail="Cannot undo: previous steps not stored")
        ps = dict(t.platform_steps or {})
        ps[target_pf] = list(steps_before)
        t.platform_steps = ps
        if target_pf == "android":
            t.steps = list(steps_before)
        t.fix_history = history[:-1]
        db.commit()
        return {"ok": True, "steps": steps_before, "target_platform": target_pf}


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
        my_steps = _steps_for_platform_record(t, "android")

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
            other_steps = _steps_for_platform_record(o, "android")
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
    target_platform: str = "android"


@app.post("/api/tests/{test_id}/apply-fix-to-related")
def apply_fix_to_related(test_id: int, payload: ApplyFixToRelatedRequest) -> dict[str, Any]:
    """Apply the same step fix to related tests that share the step prefix."""
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        project_id = t.project_id
        original = list(payload.original_steps or [])
        plat = payload.target_platform if payload.target_platform in ("android", "ios_sim") else "android"

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
            other_steps = _steps_for_platform_record(o, plat)
            shared = _shared_prefix_length(original, other_steps)
            if shared < prefix_len:
                continue
            new_steps = list(payload.fixed_steps[:prefix_len]) + other_steps[prefix_len:]
            ps = dict(o.platform_steps or {})
            ps[plat] = new_steps
            o.platform_steps = ps
            if plat == "android":
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


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        if r.status not in ("queued", "running"):
            return {"ok": True, "status": r.status, "message": f"Run already {r.status}"}
        run_engine.request_cancel(run_id)
        r.status = "cancelled"
        r.finished_at = r.finished_at or datetime.utcnow()
        db.commit()
    RunEngine._sync_batch_counters(run_id)
    return {"ok": True, "message": "Run cancelled"}


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        db.delete(r)
        db.commit()
        return {"ok": True}


# ── Batch Runs ────────────────────────────────────────────────────────


def _batch_to_out(b: BatchRun, db) -> BatchRunOut:
    child_runs = db.query(Run).filter(Run.batch_run_id == b.id).order_by(Run.id).all()
    children: list[BatchRunChildOut] = []
    for cr in child_runs:
        t = db.query(TestDefinition).filter(TestDefinition.id == cr.test_id).first() if cr.test_id else None
        children.append(BatchRunChildOut(
            run_id=cr.id,
            test_id=cr.test_id or 0,
            test_name=t.name if t else f"Run #{cr.id}",
            status=cr.status,
            started_at=cr.started_at,
            finished_at=cr.finished_at,
            error_message=cr.error_message,
        ))
    first_child = child_runs[0] if child_runs else None
    return BatchRunOut(
        id=b.id,
        project_id=b.project_id,
        mode=b.mode,
        source_id=b.source_id,
        source_name=b.source_name,
        platform=b.platform,
        status=b.status,
        total=b.total,
        passed=b.passed,
        failed=b.failed,
        build_id=first_child.build_id if first_child else None,
        device_target=first_child.device_target if first_child else "",
        started_at=b.started_at,
        finished_at=b.finished_at,
        children=children,
    )


def _update_batch_counters(db, batch_id: int) -> None:
    """Recount child statuses and update the batch row."""
    batch = db.query(BatchRun).filter(BatchRun.id == batch_id).first()
    if not batch:
        return
    children = db.query(Run).filter(Run.batch_run_id == batch_id).all()
    passed = sum(1 for c in children if c.status == "passed")
    failed = sum(1 for c in children if c.status in ("failed", "error"))
    done = passed + failed + sum(1 for c in children if c.status == "cancelled")
    batch.passed = passed
    batch.failed = failed
    if done >= batch.total:
        batch.finished_at = datetime.utcnow()
        if failed == 0 and passed == batch.total:
            batch.status = "passed"
        elif passed == 0 and failed == batch.total:
            batch.status = "failed"
        else:
            batch.status = "partial"
    elif any(c.status == "running" for c in children):
        batch.status = "running"
    db.commit()


@app.post("/api/batch-runs", response_model=BatchRunOut)
async def create_batch_run(payload: BatchRunCreate) -> BatchRunOut:
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == payload.project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")

        if payload.mode == "suite":
            suite = db.query(TestSuite).filter(TestSuite.id == payload.source_id).first()
            if not suite:
                raise HTTPException(status_code=404, detail="Suite not found")
            source_name = suite.name
            test_ids = [t.id for t in db.query(TestDefinition).filter(TestDefinition.suite_id == payload.source_id).all()]
        elif payload.mode == "collection":
            module = db.query(Module).filter(Module.id == payload.source_id).first()
            if not module:
                raise HTTPException(status_code=404, detail="Collection not found")
            source_name = module.name
            suite_ids = [s.id for s in db.query(TestSuite).filter(TestSuite.module_id == payload.source_id).all()]
            test_ids = [t.id for t in db.query(TestDefinition).filter(TestDefinition.suite_id.in_(suite_ids)).all()] if suite_ids else []
        else:
            raise HTTPException(status_code=400, detail=f"Invalid mode: {payload.mode}")

        if not test_ids:
            raise HTTPException(status_code=400, detail="No tests found for the selected scope")

        batch = BatchRun(
            project_id=payload.project_id,
            mode=payload.mode,
            source_id=payload.source_id,
            source_name=source_name,
            platform=payload.platform,
            status="queued",
            total=len(test_ids),
            started_at=datetime.utcnow(),
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)

        child_runs: list[Run] = []
        for tid in test_ids:
            r = Run(
                project_id=payload.project_id,
                build_id=payload.build_id,
                test_id=tid,
                batch_run_id=batch.id,
                status="queued",
                device_target=payload.device_target,
                platform=payload.platform,
                artifacts={},
                summary={},
            )
            db.add(r)
            child_runs.append(r)
        db.commit()
        for r in child_runs:
            db.refresh(r)

        batch.status = "running"
        db.commit()

        for r in child_runs:
            await event_bus.publish(RunEvent(run_id=r.id, type="queued", payload={"runId": r.id, "batchRunId": batch.id}))
            await run_engine.enqueue(r.id)

        return _batch_to_out(batch, db)


@app.get("/api/batch-runs/{batch_id}", response_model=BatchRunOut)
def get_batch_run(batch_id: int) -> BatchRunOut:
    with SessionLocal() as db:
        batch = db.query(BatchRun).filter(BatchRun.id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch run not found")
        return _batch_to_out(batch, db)


@app.get("/api/projects/{project_id}/batch-runs")
def list_batch_runs(project_id: int) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        batches = db.query(BatchRun).filter(BatchRun.project_id == project_id).order_by(BatchRun.id.desc()).limit(20).all()
        return [_batch_to_out(b, db).dict() for b in batches]


@app.post("/api/batch-runs/{batch_id}/cancel")
def cancel_batch_run(batch_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        batch = db.query(BatchRun).filter(BatchRun.id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch run not found")
        children = db.query(Run).filter(Run.batch_run_id == batch_id, Run.status.in_(("queued", "running"))).all()
        cancelled_count = 0
        for r in children:
            if r.status == "running":
                run_engine.request_cancel(r.id)
            r.status = "cancelled"
            r.finished_at = r.finished_at or datetime.utcnow()
            cancelled_count += 1
        db.commit()

        all_children = db.query(Run).filter(Run.batch_run_id == batch_id).all()
        passed = sum(1 for c in all_children if c.status == "passed")
        failed = sum(1 for c in all_children if c.status in ("failed", "error"))
        cancelled_n = sum(1 for c in all_children if c.status == "cancelled")
        batch.passed = passed
        batch.failed = failed
        batch.finished_at = batch.finished_at or datetime.utcnow()
        if cancelled_n == batch.total:
            batch.status = "cancelled"
        elif failed == 0 and passed == batch.total:
            batch.status = "passed"
        else:
            batch.status = "partial"
        db.commit()
    return {"ok": True, "message": f"Cancelled {cancelled_count} runs"}


# ── Artifacts ─────────────────────────────────────────────────────────

def _artifact_media_type(filename: str) -> str | None:
    """Return MIME type so browsers handle Android (.mp4) vs iOS sim (.mov) video correctly."""
    lower = filename.lower()
    if lower.endswith(".mp4"):
        return "video/mp4"
    if lower.endswith(".mov"):
        return "video/quicktime"
    if lower.endswith(".webm"):
        return "video/webm"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".xml"):
        return "application/xml"
    return None


@app.get("/api/artifacts/{project_id}/{run_id}/{name}")
def get_artifact(project_id: int, run_id: int, name: str) -> FileResponse:
    path = settings.artifacts_dir / str(project_id) / str(run_id) / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    media = _artifact_media_type(name)
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate"}
    fr_kw: dict[str, Any] = {"filename": name, "headers": headers}
    if media:
        fr_kw["media_type"] = media
    return FileResponse(str(path), **fr_kw)


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
        run_plat = (r.platform or "android") if r else "android"
        if run_plat not in ("android", "ios_sim"):
            run_plat = "android"
        export_steps = _steps_for_platform_record(t, run_plat)

        for i, s in enumerate(export_steps):
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


# ── Script / Sheet import & Reports hierarchy ─────────────────────────-

_NO_SELECTOR_STEP_TYPES = frozenset({"wait", "takeScreenshot", "hideKeyboard", "swipe", "gherkin_raw", "python_raw"})


def _validate_test_cases(test_cases: list[dict]) -> list[str]:
    warnings: list[str] = []
    for tc in test_cases:
        bad = 0
        for s in tc.get("steps", []) or []:
            t = s.get("type")
            if t in _NO_SELECTOR_STEP_TYPES:
                continue
            sel = s.get("selector") or {}
            if not (isinstance(sel, dict) and sel.get("value")):
                bad += 1
        if bad:
            name = tc.get("name", "?")
            warnings.append(f"'{name}': {bad} step(s) have empty selectors")
    return warnings


def _groovy_scripts_for_sheet_cases(test_cases: list[dict], filename: str) -> list[dict[str, Any]]:
    """Katalon Groovy source per test case; mutates each tc with groovy_script."""
    stem = safe_katalon_name(Path(filename).stem) if filename else "ImportedSheet"
    scripts: list[dict[str, Any]] = []
    for tc in test_cases:
        steps = tc.get("steps") or []
        name = tc.get("name", "Unnamed")
        g_content = steps_to_groovy(
            test_name=name,
            steps=steps,
            screen_name=stem,
            source_hint=f"Generated from {filename or 'sheet'}",
        )
        tc["groovy_script"] = g_content
        scripts.append({"name": safe_katalon_name(name) + ".groovy", "content": g_content})
    return scripts


async def _ai_complete_gherkin(
    raw_steps: list, platform: str, source: str, api_key: str, model: str
) -> list[dict]:
    if not api_key:
        return [{"name": "Imported from Gherkin", "steps": raw_steps, "import": True, "acceptance_criteria": ""}]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    prompt = (
        "Convert this Gherkin feature file into Appium test cases. "
        f"Platform: {platform}. Return ONLY JSON: "
        '{"test_cases": [{"name": "...", "steps": [...], "acceptance_criteria": "..."}]}. '
        "Use accessibilityId selectors where possible.\n\n" + source[:6000]
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(url, json=body)
        if r.status_code != 200:
            return [{"name": "Imported from Gherkin", "steps": raw_steps, "import": True, "acceptance_criteria": ""}]
        try:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            return [dict(tc, **{"import": True}) for tc in parsed.get("test_cases", [])]
        except (KeyError, IndexError, json.JSONDecodeError, TypeError):
            return [{"name": "Imported from Gherkin", "steps": raw_steps, "import": True, "acceptance_criteria": ""}]


async def _ai_parse_python_script(source: str, platform: str, api_key: str, model: str) -> list[dict]:
    if not api_key:
        return []
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    prompt = (
        f"Parse this Appium Python test script into test cases for platform {platform}. "
        "Map driver.find_element+click to tap, send_keys to type, WebDriverWait+visibility to waitForVisible, assertions to assertText/assertVisible. "
        "Available step types: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
        "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
        "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
        "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n"
        'Return ONLY JSON: {"test_cases": [{"name": "...", "steps": [...], "acceptance_criteria": "..."}]}.\n\n'
        + source[:6000]
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(url, json=body)
        if r.status_code != 200:
            return []
        try:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            return [dict(tc, **{"import": True}) for tc in parsed.get("test_cases", [])]
        except (KeyError, IndexError, json.JSONDecodeError, TypeError):
            return []


def _normalize_ai_katalon_cases(
    parsed: dict[str, Any], stem: str, fallback_cases: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    cases = parsed.get("test_cases")
    if not isinstance(cases, list) or not cases:
        return None
    out: list[dict[str, Any]] = []
    for i, tc in enumerate(cases):
        if not isinstance(tc, dict):
            return None
        name = str(tc.get("name") or "").strip() or stem
        steps = tc.get("steps")
        if not isinstance(steps, list) or len(steps) == 0:
            fb = (
                fallback_cases[i]
                if i < len(fallback_cases)
                else (fallback_cases[0] if fallback_cases else None)
            )
            if not fb:
                return None
            tc = {**fb, "name": name}
        else:
            tc["name"] = name
        tc["import"] = True
        tc.setdefault("acceptance_criteria", str(tc.get("acceptance_criteria") or "") or "")
        out.append(tc)
    return out


def _katalon_ai_locator_looks_like_ui_label(value: str, leaves: set[str]) -> bool:
    v = (value or "").strip()
    if not v:
        return True
    if v in leaves:
        return False
    if re.search(r"\s&\s", v) or "  " in v:
        return True
    # Visible copy: phrase with spaces, no OR-style token (underscore / camel prefix)
    if " " in v and "_" not in v:
        return True
    return False


def _merge_katalon_ai_steps_with_heuristic(
    ai_steps: list[dict[str, Any]],
    heuristic_steps: list[dict[str, Any]],
    leaves: set[str],
) -> list[dict[str, Any]]:
    """Keep AI step types/order where reasonable; restore OR leaf selectors from heuristic when AI used UI labels."""
    out: list[dict[str, Any]] = []
    for i, s in enumerate(ai_steps):
        merged: dict[str, Any] = dict(s)
        h = heuristic_steps[i] if i < len(heuristic_steps) else None
        hs = h.get("selector") if h else None
        hv = hs.get("value") if isinstance(hs, dict) else None
        sel = merged.get("selector")
        if isinstance(sel, dict) and isinstance(hs, dict) and hv and str(hv).strip():
            av = str(sel.get("value") or "").strip()
            if av != str(hv).strip():
                if _katalon_ai_locator_looks_like_ui_label(av, leaves) or (hv in leaves and av not in leaves):
                    merged["selector"] = {
                        **sel,
                        "using": hs.get("using") or sel.get("using") or "accessibilityId",
                        "value": hv,
                    }
        elif isinstance(sel, dict) and not str(sel.get("value") or "").strip() and hv:
            merged["selector"] = dict(hs) if isinstance(hs, dict) else merged.get("selector")
        out.append(merged)
    return out


def _apply_katalon_locator_snap(
    ai_cases: list[dict[str, Any]],
    fallback_cases: list[dict[str, Any]],
    source: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Snap AI selectors to Object Repository leaves; if AI invents far more steps than the script,
    trust the rule-based parse for this file (common model failure on callTestCase / imports).
    """
    notes: list[str] = []
    leaves, _ = katalon_or_leaves_and_aliases(source)
    out: list[dict[str, Any]] = []
    for ci, tc in enumerate(ai_cases):
        if not isinstance(tc, dict):
            out.append(tc)
            continue
        fb = (
            fallback_cases[ci]
            if ci < len(fallback_cases)
            else (fallback_cases[0] if fallback_cases else {})
        )
        h_steps = (fb.get("steps") or []) if isinstance(fb, dict) else []
        steps = tc.get("steps") or []
        if not isinstance(steps, list) or not h_steps:
            out.append(tc)
            continue
        n_ai, n_h = len(steps), len(h_steps)
        if n_h > 0 and n_ai > max(20, n_h * 3):
            name = str(tc.get("name") or "?")
            out.append({**tc, "steps": list(h_steps)})
            notes.append(
                f'"{name}": AI produced {n_ai} steps vs {n_h} from script; using rule-based parse '
                f"(set AI API key and re-import if you need AI — or shorten script for context)."
            )
            continue
        out.append({**tc, "steps": _merge_katalon_ai_steps_with_heuristic(steps, h_steps, leaves)})
    return out, notes


async def _ai_parse_katalon_source(
    source: str,
    platform: str,
    logical_path: str,
    api_key: str,
    model: str,
    fallback_cases: list[dict[str, Any]],
    *,
    object_repo: Optional[dict[str, dict[str, str]]] = None,
    xml_context: str = "",
    platform_selector_rules: str = "",
) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """Parse Katalon Groovy/Java mobile scripts with Gemini; None = caller uses heuristic fallback."""
    extra_warnings: list[str] = []
    if not api_key or not source.strip():
        return None, extra_warnings
    stem = Path(logical_path).stem or "Imported"
    max_chars = 56_000
    src = source if len(source) <= max_chars else source[:max_chars] + "\n\n// ... [truncated for AI context] ...\n"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    system = (
        "You parse Katalon Studio Mobile test scripts (Groovy/Java calling Mobile.*) into JSON for Appium.\n"
        f"Target platform: {platform} (android = UiAutomator2, ios_sim = XCUITest).\n\n"
        "Output ONLY valid JSON:\n"
        '{"test_cases":[{"name":"...","steps":[...],"acceptance_criteria":"..."}]}\n\n'
        "Step object shape:\n"
        '{"type":"<step_type>",'
        '"selector":{"using":"accessibilityId|id|xpath|className","value":"..."},'
        '"text":"...","ms":1000,"expect":"...","meta":{...}}\n'
        "Available step types: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
        "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
        "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
        "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n"
        "Omit selector on: wait, takeScreenshot, tapByCoordinates, pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, getPageSource.\n\n"
        "SELECTOR RULES (mandatory):\n"
        "- For EVERY element, resolve TestObject/def variables: `TestObject x = findTestObject('Object Repository/.../leafName')`. "
        "The locator token is the substring AFTER the final '/' — e.g. txtBox_libiPrompt, btn_TextEnterButton. "
        "Set selector.value to that leaf EXACTLY: keep underscores, hyphens, camelCase; never replace with human-readable UI text.\n"
        "- Mobile.tap(txtPrompt, ...) means: look up txtPrompt's findTestObject binding and use THAT leaf as selector.value.\n"
        "- FORBIDDEN as selector.value: strings from base.logInfo, user-visible setText bodies, screenshot file names, "
        "or prettified labels like 'Selfcare & transactions' unless the script literally uses that exact token in findTestObject.\n"
        "- If the script only has callTestCase(findTestCase(...)) lines and no local Mobile.* steps, output minimal waits — "
        "do NOT invent a long unrelated checkout flow.\n"
        "- Resource ids in literals/comments: use using=id with full resource id.\n"
        "- For verifyElementText, set expect from the script's expected string.\n"
        "- Map waitForElementPresent / retryWaitForElementPresent to waitForVisible; timeout seconds → ms.\n"
        "- Map Mobile.delay / delay to wait with ms.\n"
        "- scrollToText: use swipe (direction in text, usually down) and waitForVisible/assertVisible for the target when you can name a selector; "
        "otherwise minimal swipe + wait.\n"
        "- callTestCase: use wait steps to reflect handoff, keep subsequent flow.\n"
        "- System keyboard keys: keyboardAction, not tap.\n\n"
        "Use one test_cases entry unless the file clearly defines multiple independent tests (e.g. several unrelated test methods). "
        f'Default name: "{stem}".\n'
        f"Source file path hint: {logical_path}\n"
    )

    # Append Object Repository locator table when available
    if object_repo:
        repo_lines = [f"  {leaf}: using={info['strategy']} value={info['value']}" for leaf, info in sorted(object_repo.items())]
        system += (
            "\n--- OBJECT REPOSITORY (leafName → real locator) ---\n"
            "When a findTestObject leaf matches an entry below, use its exact strategy and value "
            "instead of guessing accessibilityId:\n"
            + "\n".join(repo_lines[:500]) + "\n"
        )

    # Append Screen Library XML context as fallback grounding
    if xml_context:
        system += (
            "\n--- SCREEN LIBRARY XML (captured page sources) ---\n"
            "Use the elements below to ground selectors to real attributes from the device. "
            "Prefer resource-id, content-desc, or accessibility id over xpath.\n"
            + xml_context[:30_000] + "\n"
        )

    # Inject platform-specific Compose/SwiftUI selector rules when Screen Library context is present
    if platform_selector_rules:
        system += "\n" + platform_selector_rules

    body = {
        "contents": [{"parts": [{"text": system + "\n--- SOURCE ---\n" + src}]}],
        "generationConfig": {"temperature": 0.08, "responseMimeType": "application/json"},
    }
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, json=body)
        if r.status_code != 200:
            return None, extra_warnings
        text = _gemini_extract_text(r.json())
        merged = _normalize_ai_katalon_cases(json.loads(text), stem, fallback_cases)
        if merged:
            merged, snap_notes = _apply_katalon_locator_snap(merged, fallback_cases, source)
            extra_warnings.extend(snap_notes)
            return merged, extra_warnings
    except (HTTPException, json.JSONDecodeError, TypeError, KeyError, IndexError, httpx.HTTPError, Exception):
        return None, extra_warnings
    return None, extra_warnings


async def _rewrite_katalon_parsed_files_with_ai(
    parsed_files: list[ParsedFile],
    platform: str,
    api_key: str,
    model: str,
    *,
    object_repo: Optional[dict[str, dict[str, str]]] = None,
    xml_context: str = "",
    platform_selector_rules: str = "",
) -> None:
    if not api_key:
        return
    sem = asyncio.Semaphore(4)

    async def one(pf: ParsedFile) -> None:
        if pf.error or pf.extension not in (".groovy", ".java"):
            return
        raw = (pf.raw_text or "").strip()
        if not raw:
            return
        async with sem:
            fallback = list(pf.test_cases)
            ai_cases, ai_notes = await _ai_parse_katalon_source(
                raw, platform, pf.path, api_key, model, fallback,
                object_repo=object_repo, xml_context=xml_context,
                platform_selector_rules=platform_selector_rules,
            )
        if ai_cases:
            pf.test_cases = ai_cases
            pf.warnings.extend(ai_notes)
            if not any("parsed with AI" in w for w in pf.warnings):
                pf.warnings.append("Katalon steps parsed with AI — verify selectors on a real device.")

    await asyncio.gather(*(one(pf) for pf in parsed_files))


def _sheet_fallback_chunk(chunk: list[dict]) -> list[dict]:
    return [
        {
            "name": r["name"],
            "steps": sheet_row_combined_steps(r),
            "acceptance_criteria": r.get("expected", "") or "",
            "import": True,
        }
        for r in chunk
    ]


async def _ai_complete_sheet_rows(rows: list, platform: str, api_key: str, model: str) -> list[dict]:
    if not rows:
        return []
    if not api_key:
        return _sheet_fallback_chunk(rows)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    CHUNK = 12
    all_out: list[dict] = []

    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        n = len(chunk)
        instruction = (
            f"Platform: {platform}. Convert manual test sheet rows into Appium mobile test steps (JSON).\n"
            f"Input: exactly {n} rows in order. Each row uses:\n"
            "- name: test case title\n"
            "- steps_description: free-text steps (may be multi-line)\n"
            "- expected: expected result (must drive a final assertion when it describes an outcome)\n"
            "- selector_value / selector_strategy / input_value: optional locators\n\n"
            "Output ONLY valid JSON:\n"
            '{"test_cases":[{"name":"...","steps":[...],"acceptance_criteria":"..."}]}\n\n'
            f"Rules:\n"
            f"- test_cases MUST have exactly {n} entries in the SAME ORDER as the rows below.\n"
            "- For each row, set acceptance_criteria from the Expected column (expected).\n"
            "- Expand steps_description into atomic steps: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
            "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
            "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
            "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n"
            "- Include selector_value in the right steps when it clearly identifies a control.\n"
            "- End with assertText or assertVisible reflecting the Expected outcome when applicable.\n"
            "- Each step: {\"type\":\"...\",\"selector\":{\"using\":\"accessibilityId|id|xpath\",\"value\":\"...\"},\"text\",\"ms\",\"expect\"}\n\n"
            "Rows JSON:\n" + json.dumps(chunk, indent=2)
        )
        body = {
            "contents": [{"parts": [{"text": instruction}]}],
            "generationConfig": {"temperature": 0.25, "responseMimeType": "application/json"},
        }
        parsed_ok = False
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=body)
            if resp.status_code == 200:
                text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                cases = parsed.get("test_cases")
                if isinstance(cases, list) and len(cases) == n:
                    for tc, row in zip(cases, chunk):
                        tc["name"] = row["name"]
                        steps = tc.get("steps")
                        if not isinstance(steps, list) or not steps:
                            tc["steps"] = sheet_row_combined_steps(row)
                        tc["acceptance_criteria"] = (tc.get("acceptance_criteria") or row.get("expected") or "") or ""
                        tc["import"] = True
                    all_out.extend(cases)
                    parsed_ok = True
        except (KeyError, IndexError, json.JSONDecodeError, TypeError, httpx.HTTPError, Exception):
            pass
        if not parsed_ok:
            all_out.extend(_sheet_fallback_chunk(chunk))

    return all_out


@app.post("/api/projects/{project_id}/import/script")
async def import_script(
    project_id: int,
    suite_id: int,
    platform: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Parse script; return preview test cases. Client calls confirm to persist."""
    content = await file.read()
    filename = (file.filename or "").lower()
    source = content.decode("utf-8", errors="replace")

    s = _load_settings()
    api_key, model = _ai_creds(s)

    katalon_import_mode: str | None = None
    katalon_import_notes: list[str] = []
    if filename.endswith(".groovy") or filename.endswith(".java"):
        raw_steps = parse_groovy(source)
        fallback_cases = group_steps_into_test_cases(raw_steps, file.filename or "script")
        if api_key:
            ai_cases, ai_notes = await _ai_parse_katalon_source(
                source, platform, file.filename or "script", api_key, model, fallback_cases
            )
            katalon_import_notes.extend(ai_notes)
            if ai_cases:
                test_cases = ai_cases
                katalon_import_mode = "ai"
            else:
                test_cases = fallback_cases
                katalon_import_mode = "heuristic"
        else:
            test_cases = fallback_cases
            katalon_import_mode = "heuristic"
    elif filename.endswith(".feature"):
        raw_steps = parse_gherkin(source)
        test_cases = await _ai_complete_gherkin(raw_steps, platform, source, api_key, model)
    elif filename.endswith(".py"):
        test_cases = await _ai_parse_python_script(source, platform, api_key, model)
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type (use .groovy, .java, .feature, .py)")

    _ = suite_id  # reserved for validation / future use
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")

    warn = _validate_test_cases(test_cases) + katalon_import_notes
    out: dict[str, Any] = {
        "test_cases": test_cases,
        "filename": file.filename or "",
        "warnings": warn,
    }
    if katalon_import_mode is not None:
        out["katalon_import_mode"] = katalon_import_mode
    return out


@app.post("/api/projects/{project_id}/import/script/confirm")
def confirm_script_import(project_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Persist selected test cases after user reviews the parsed preview."""
    suite_id = payload.get("suite_id")
    if suite_id is not None:
        try:
            suite_id = int(suite_id)
        except (TypeError, ValueError):
            suite_id = None
    selected_cases = payload.get("test_cases", [])

    created: list[dict] = []
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")

        for tc in selected_cases:
            if not tc.get("import", True):
                continue
            name = tc.get("name")
            steps = tc.get("steps")
            if not name or not isinstance(steps, list):
                continue
            if steps and all(s.get("type") == "python_raw" for s in steps):
                continue
            st = list(steps)
            t = TestDefinition(
                project_id=project_id,
                suite_id=suite_id,
                name=str(name)[:200],
                steps=st,
                platform_steps={"android": st, "ios_sim": []},
                acceptance_criteria=tc.get("acceptance_criteria") or None,
            )
            db.add(t)
            db.flush()
            created.append({"id": t.id, "name": t.name})
        db.commit()

    return {"created": len(created), "tests": created}


@app.post("/api/projects/{project_id}/import/sheet")
async def import_sheet(
    project_id: int,
    suite_id: int,
    platform: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Parse Excel/CSV: structured steps from Steps + Expected columns, plus Katalon Groovy per case."""
    content = await file.read()
    filename = file.filename or ""

    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")

    fn_lower = filename.lower()
    if not (fn_lower.endswith(".csv") or fn_lower.endswith(".xlsx")):
        raise HTTPException(status_code=400, detail="Unsupported file (use .csv or .xlsx)")

    try:
        rows = parse_test_sheet(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    s = _load_settings()
    api_key, model = _ai_creds(s)

    test_cases = await _ai_complete_sheet_rows(rows, platform, api_key, model)
    scripts = _groovy_scripts_for_sheet_cases(test_cases, filename)
    _ = suite_id
    warnings = _validate_test_cases(test_cases)
    return {
        "test_cases": test_cases,
        "scripts": scripts,
        "filename": filename,
        "row_count": len(rows),
        "warnings": warnings,
    }


@app.post("/api/projects/{project_id}/import/zip")
async def import_zip(
    project_id: int,
    platform: str,
    file: UploadFile = File(...),
    folder_id: Optional[int] = None,
    build_ids: Optional[str] = None,
) -> dict[str, Any]:
    """Parse a ZIP of scripts; preview grouped by folder (suite). Does not persist."""
    with SessionLocal() as db:
        if not db.query(Project).filter(Project.id == project_id).first():
            raise HTTPException(status_code=404, detail="Project not found")

    content = await file.read()
    fn = (file.filename or "").lower()
    if not fn.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Expected a .zip file")

    parsed_files = parse_zip(content)
    obj_repo = parse_object_repo_from_zip(content)
    katalon = parse_katalon_project(content)

    xml_ctx = ""
    sel_rules = ""
    if not obj_repo and folder_id:
        _bid_list = [int(b) for b in (build_ids or "").split(",") if b.strip().isdigit()]
        with SessionLocal() as db:
            q = db.query(ScreenLibrary).filter(
                ScreenLibrary.folder_id == folder_id,
                ScreenLibrary.xml_snapshot.isnot(None),
            )
            if _bid_list:
                q = q.filter(ScreenLibrary.build_id.in_(_bid_list))
            screens = q.all()
            if screens:
                xml_ctx = _build_xml_context(screens)
                if platform == "android":
                    sel_rules = _android_selector_generation_rules(screens)
                elif platform == "ios_sim":
                    sel_rules = _ios_selector_generation_rules(screens)

    s = _load_settings()
    zip_ai_key, zip_model = _ai_creds(s)
    await _rewrite_katalon_parsed_files_with_ai(
        parsed_files, platform, zip_ai_key, zip_model,
        object_repo=obj_repo or None, xml_context=xml_ctx,
        platform_selector_rules=sel_rules,
    )

    # Build tc_id → parsed test case lookup
    tc_by_id: dict[str, dict[str, Any]] = {}
    for pf in parsed_files:
        norm = _normalize_katalon_path(pf.path)
        tc_id = _tc_id_from_groovy_path(norm)
        for tc in pf.test_cases:
            steps = tc.get("steps") or []
            if any(s.get("type") == "python_raw" for s in steps):
                tc["import"] = False
            else:
                tc.setdefault("import", True)
            tc["source_file"] = pf.path
            # Enrich with .tc metadata
            meta = katalon.tc_metadata.get(tc_id)
            if meta:
                if meta.description and not tc.get("acceptance_criteria"):
                    tc["acceptance_criteria"] = meta.description
                if meta.tags:
                    tc["tags"] = meta.tags
                if meta.comment and meta.comment != tc.get("name"):
                    tc.setdefault("comment", meta.comment)
            tc_by_id[tc_id] = tc

    # Group by .ts suites if available, else fall back to folder hierarchy
    groups: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    assigned_tc_ids: set[str] = set()

    if katalon.suites:
        for suite in katalon.suites:
            suite_cases: list[dict[str, Any]] = []
            for ref in suite.tc_ids:
                tc = tc_by_id.get(ref)
                if tc:
                    tc["suggested_suite"] = suite.name
                    suite_cases.append(tc)
                    assigned_tc_ids.add(ref)
            if suite_cases:
                groups[suite.name] = suite_cases

    # Anything not claimed by a .ts suite goes into folder-derived groups
    for pf in parsed_files:
        norm = _normalize_katalon_path(pf.path)
        tc_id = _tc_id_from_groovy_path(norm)
        if tc_id in assigned_tc_ids:
            continue
        tc = tc_by_id.get(tc_id)
        if not tc:
            continue
        suite_name = extract_folder_name(pf.path)
        tc["suggested_suite"] = suite_name
        groups.setdefault(suite_name, []).append(tc)
        assigned_tc_ids.add(tc_id)

    total_cases = sum(len(tcs) for tcs in groups.values())

    for pf in parsed_files:
        if pf.error:
            warnings.append(f"{pf.path}: {pf.error}")
        warnings.extend(pf.warnings)

    # Build collection → suite mapping for the frontend
    collections_map: dict[str, list[str]] = {}
    suite_names = {s.name for s in katalon.suites}
    for coll in katalon.collections:
        coll_suites = [ref.split("/")[-1] for ref in coll.suite_refs if ref.split("/")[-1] in suite_names]
        if coll_suites:
            collections_map[coll.name] = coll_suites

    grounding = "object_repo" if obj_repo else ("screen_library" if xml_ctx else "none")
    if obj_repo:
        warnings.insert(0, f"Object Repository: {len(obj_repo)} locator(s) extracted from .rs files")
    if katalon.suites:
        warnings.insert(0, f"Katalon project: {len(katalon.suites)} suite(s), {len(katalon.collections)} collection(s), {len(katalon.tc_metadata)} .tc metadata file(s)")

    return {
        "groups": groups,
        "total_cases": total_cases,
        "total_files": len(parsed_files),
        "warnings": warnings,
        "grounding": grounding,
        "object_repo_count": len(obj_repo),
        "collections": collections_map,
        "katalon_detected": bool(katalon.suites or katalon.collections),
        "files": [
            {
                "path": pf.path,
                "cases_count": len(pf.test_cases),
                "status": "error" if pf.error else ("warn" if pf.warnings else "ok"),
            }
            for pf in parsed_files
        ],
    }


@app.post("/api/projects/{project_id}/import/folder")
async def import_folder(
    project_id: int,
    platform: str,
    files: list[UploadFile] = File(...),
    folder_id: Optional[int] = None,
    build_ids: Optional[str] = None,
) -> dict[str, Any]:
    """Multiple files from webkitdirectory upload."""
    with SessionLocal() as db:
        if not db.query(Project).filter(Project.id == project_id).first():
            raise HTTPException(status_code=404, detail="Project not found")

    if not files:
        raise HTTPException(
            status_code=400,
            detail="No files received. Each part must use the multipart field name 'files' (folder picker).",
        )

    file_data: list[tuple[str, bytes]] = []
    for f in files:
        raw = await f.read()
        rel = f.filename or "file"
        file_data.append((rel, raw))

    parsed_files = parse_folder_files(file_data)
    obj_repo = parse_object_repo_from_files(file_data)

    xml_ctx = ""
    sel_rules = ""
    if not obj_repo and folder_id:
        _bid_list = [int(b) for b in (build_ids or "").split(",") if b.strip().isdigit()]
        with SessionLocal() as db:
            q = db.query(ScreenLibrary).filter(
                ScreenLibrary.folder_id == folder_id,
                ScreenLibrary.xml_snapshot.isnot(None),
            )
            if _bid_list:
                q = q.filter(ScreenLibrary.build_id.in_(_bid_list))
            screens = q.all()
            if screens:
                xml_ctx = _build_xml_context(screens)
                if platform == "android":
                    sel_rules = _android_selector_generation_rules(screens)
                elif platform == "ios_sim":
                    sel_rules = _ios_selector_generation_rules(screens)

    s = _load_settings()
    folder_ai_key, folder_model = _ai_creds(s)
    await _rewrite_katalon_parsed_files_with_ai(
        parsed_files, platform, folder_ai_key, folder_model,
        object_repo=obj_repo or None, xml_context=xml_ctx,
        platform_selector_rules=sel_rules,
    )

    groups: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    total_cases = 0

    for pf in parsed_files:
        suite_name = extract_folder_name(pf.path)
        groups.setdefault(suite_name, [])
        for tc in pf.test_cases:
            steps = tc.get("steps") or []
            if any(s.get("type") == "python_raw" for s in steps):
                warnings.append(f"{pf.path}: Python script needs AI completion")
                tc["import"] = False
            else:
                tc.setdefault("import", True)
            tc["source_file"] = pf.path
            tc["suggested_suite"] = suite_name
            groups[suite_name].append(tc)
            total_cases += 1
        if pf.error:
            warnings.append(f"{pf.path}: {pf.error}")
        warnings.extend(pf.warnings)

    grounding = "object_repo" if obj_repo else ("screen_library" if xml_ctx else "none")
    if obj_repo:
        warnings.insert(0, f"Object Repository: {len(obj_repo)} locator(s) extracted from .rs files")

    return {
        "groups": groups,
        "total_cases": total_cases,
        "total_files": len(parsed_files),
        "warnings": warnings,
        "grounding": grounding,
        "object_repo_count": len(obj_repo),
        "files": [
            {
                "path": pf.path,
                "cases_count": len(pf.test_cases),
                "status": "error" if pf.error else ("warn" if pf.warnings else "ok"),
            }
            for pf in parsed_files
        ],
    }


@app.post("/api/projects/{project_id}/import/zip/confirm")
def confirm_zip_import(project_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Persist ZIP/folder preview. Optional suite_map, auto-create suites under module_id.
    When `collections` is provided (from a Katalon project), modules are created per collection
    and suites are placed underneath the appropriate module."""
    suite_map_raw = payload.get("suite_map") or {}
    suite_map: dict[str, int] = {}
    for k, v in suite_map_raw.items():
        if v is None:
            continue
        try:
            suite_map[str(k)] = int(v)
        except (TypeError, ValueError):
            continue

    mod_raw = payload.get("module_id")
    module_id: Optional[int] = None
    if mod_raw is not None:
        try:
            module_id = int(mod_raw)
        except (TypeError, ValueError):
            module_id = None

    collections_raw: dict[str, list[str]] = payload.get("collections") or {}

    test_cases: list[dict[str, Any]] = payload.get("test_cases", [])
    created_modules: dict[str, int] = {}
    created_suites: dict[str, int] = {}
    created_tests: list[dict[str, Any]] = []

    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")

        # Pre-create modules from Katalon collections
        suite_to_module: dict[str, int] = {}
        if collections_raw:
            for coll_name, coll_suites in collections_raw.items():
                mod = Module(project_id=project_id, name=str(coll_name)[:200])
                db.add(mod)
                db.flush()
                created_modules[coll_name] = mod.id
                for s_name in coll_suites:
                    suite_to_module[s_name] = mod.id

        # If no collections but a module_id is given, use a single fallback module
        # for suites not claimed by any collection
        fallback_module_id = module_id

        # Also create a default "Imported" module for orphan suites when collections exist
        if collections_raw:
            orphan_mod = Module(project_id=project_id, name="Imported (uncategorised)")
            db.add(orphan_mod)
            db.flush()
            fallback_module_id = orphan_mod.id

        for tc in test_cases:
            if not tc.get("import", True):
                continue
            steps = tc.get("steps")
            if not isinstance(steps, list) or not steps:
                continue
            if all(s.get("type") == "python_raw" for s in steps):
                continue
            name = tc.get("name") or "Imported test"
            suite_name = str(tc.get("suggested_suite", "Imported"))[:200]

            suite_id = suite_map.get(suite_name)
            if suite_id is None:
                suite_id = created_suites.get(suite_name)

            if suite_id is None:
                target_module_id = suite_to_module.get(suite_name, fallback_module_id)
                if target_module_id is not None:
                    new_suite = TestSuite(module_id=target_module_id, name=suite_name)
                    db.add(new_suite)
                    db.flush()
                    created_suites[suite_name] = new_suite.id
                    suite_id = new_suite.id

            ac = tc.get("acceptance_criteria")
            st = list(steps)
            t = TestDefinition(
                project_id=project_id,
                suite_id=suite_id,
                name=str(name)[:200],
                steps=st,
                platform_steps={"android": st, "ios_sim": []},
                acceptance_criteria=str(ac) if ac else None,
            )
            db.add(t)
            db.flush()
            created_tests.append({"id": t.id, "name": t.name, "suite": suite_name})

        # Remove the orphan module if nothing was placed in it
        if collections_raw and fallback_module_id and not any(
            db.query(TestSuite).filter(TestSuite.module_id == fallback_module_id).first()
            for _ in [1]
        ):
            orphan = db.query(Module).filter(Module.id == fallback_module_id).first()
            if orphan:
                db.delete(orphan)

        db.commit()

    return {
        "created": len(created_tests),
        "created_suites": list(created_suites.keys()),
        "created_modules": list(created_modules.keys()),
        "tests": created_tests,
    }


@app.post("/api/projects/{project_id}/generate/katalon-zip")
def generate_katalon_zip_endpoint(project_id: int, payload: dict[str, Any] = Body(...)) -> StreamingResponse:
    """Build Katalon project ZIP from saved test IDs and/or inline test case dicts."""
    test_case_ids = payload.get("test_case_ids") or []
    inline_cases = payload.get("test_cases") or []
    project_name = str(payload.get("project_name") or "QA_Project")

    cases_to_export: list[dict[str, Any]] = list(inline_cases)

    with SessionLocal() as db:
        for tc_id in test_case_ids:
            try:
                tid = int(tc_id)
            except (TypeError, ValueError):
                continue
            t = (
                db.query(TestDefinition)
                .filter(TestDefinition.id == tid, TestDefinition.project_id == project_id)
                .first()
            )
            if not t:
                continue
            suite_name = "Exported"
            if t.suite_id:
                su = db.query(TestSuite).filter(TestSuite.id == t.suite_id).first()
                if su:
                    suite_name = su.name
            cases_to_export.append(
                {
                    "name": t.name,
                    "steps": _steps_for_platform_record(t, "android"),
                    "acceptance_criteria": t.acceptance_criteria or "",
                    "suite_name": suite_name,
                }
            )

    if not cases_to_export:
        raise HTTPException(status_code=400, detail="No test cases to export")

    zip_bytes = generate_katalon_zip(project_name, cases_to_export)
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", project_name)[:80] or "katalon"

    return StreamingResponse(
        BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_katalon.zip"'},
    )


# ── Reports v2 — test-case-first endpoints ─────────────────────────────

def _run_window(db, suite_or_test_ids: list[int], days: int, platform: str, *, by_suite: bool = True):
    """Return runs within the time window, optionally filtered by platform."""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    q = db.query(Run).filter(Run.started_at >= cutoff)
    if platform:
        q = q.filter(Run.platform == platform)
    if by_suite:
        q = q.filter(Run.test_id.in_(
            db.query(TestDefinition.id).filter(TestDefinition.suite_id.in_(suite_or_test_ids))
        ))
    else:
        q = q.filter(Run.test_id.in_(suite_or_test_ids))
    return q.order_by(Run.id.asc()).all()


def _steps_for_platform_record(t: TestDefinition, platform: str) -> list[dict]:
    ps = t.platform_steps or {}
    if isinstance(ps, dict):
        s = ps.get(platform) or ps.get("android") or []
        if s:
            return list(s)
    return list(t.steps or [])


def _prereq_step_count(t: TestDefinition, db, platform: str = "") -> int:
    """Return the number of prerequisite steps prepended by the runner.
    If platform given, use that platform's prereq steps; else use the max."""
    if not t.prerequisite_test_id or t.prerequisite_test_id == t.id:
        return 0
    prereq = db.query(TestDefinition).filter(TestDefinition.id == t.prerequisite_test_id).first()
    if not prereq:
        return 0
    if platform:
        return len(_steps_for_platform_record(prereq, platform))
    return max(len(_steps_for_platform_record(prereq, "android")), len(_steps_for_platform_record(prereq, "ios_sim")))


def _test_health_row(t: TestDefinition, test_runs: list[Run], days: int, prereq_offset: int = 0, prereq_def: "Optional[TestDefinition]" = None) -> dict[str, Any]:
    """Build one test-case health row from its runs in the window.
    prereq_offset: fallback count; prereq_def: prerequisite test for platform-aware offset."""
    if not test_runs:
        has_android = bool(_steps_for_platform_record(t, "android"))
        has_ios = bool(_steps_for_platform_record(t, "ios_sim"))
        plat = "android" if has_android and not has_ios else "ios_sim" if has_ios and not has_android else "android"
        return {
            "id": t.id, "name": t.name, "status": "not_run",
            "steps_ran": 0, "steps_total": max(len(_steps_for_platform_record(t, "android")), len(_steps_for_platform_record(t, "ios_sim"))),
            "platform": plat,
            "pass_rate_pct": 0, "fail_streak": 0,
            "last_passed_at": None, "ai_fixes_count": len(t.fix_history or []),
            "run_history": [], "last_failed_run": None,
        }

    platforms_seen = set(r.platform for r in test_runs if r.platform)
    plat = "both" if len(platforms_seen) > 1 else (platforms_seen.pop() if platforms_seen else "android")

    passed = sum(1 for r in test_runs if r.status == "passed")
    total = len(test_runs)
    rate = round(100 * passed / total) if total else 0

    # fail streak (from newest)
    streak = 0
    for r in reversed(test_runs):
        if r.status in ("failed", "error"):
            streak += 1
        else:
            break

    # flaky: both pass and fail, no long consecutive fail streak
    has_pass = any(r.status == "passed" for r in test_runs)
    has_fail = any(r.status in ("failed", "error") for r in test_runs)
    if has_pass and has_fail and streak <= 2:
        status = "flaky"
    elif streak > 0 and not has_pass:
        status = "failing"
    elif streak > 0:
        status = "failing"
    else:
        status = "passing"

    last_passed = None
    for r in reversed(test_runs):
        if r.status == "passed":
            last_passed = r.finished_at.isoformat() if r.finished_at else None
            break

    # steps_total from test definition only (excludes prerequisite)
    steps_total = max(len(_steps_for_platform_record(t, "android")), len(_steps_for_platform_record(t, "ios_sim")))
    # steps_ran: subtract prerequisite steps from run data using the run's actual platform
    last = test_runs[-1]
    sr = last.summary or {}
    step_results = sr.get("stepResults") or []
    all_ran = sum(1 for s in step_results if s.get("status") in ("passed", "failed"))
    run_pf = (last.platform or "android").strip() or "android"
    actual_offset = len(_steps_for_platform_record(prereq_def, run_pf)) if prereq_def else prereq_offset
    steps_ran = max(0, all_ran - actual_offset)

    # last failed run detail (skip prerequisite steps)
    last_failed_run = None
    for r in reversed(test_runs):
        if r.status in ("failed", "error"):
            rsummary = r.summary or {}
            rsteps = rsummary.get("stepResults") or []
            arts = r.artifacts or {}
            screenshots = arts.get("screenshots") or []
            # Slice off prerequisite steps using this run's platform
            r_pf = (r.platform or "android").strip() or "android"
            r_offset = len(_steps_for_platform_record(prereq_def, r_pf)) if prereq_def else prereq_offset
            own_rsteps = rsteps[r_offset:]
            own_screenshots = screenshots[r_offset:]
            step_detail = []
            for si, sr_item in enumerate(own_rsteps):
                shot = own_screenshots[si] if si < len(own_screenshots) else None
                step_detail.append({
                    "index": si,
                    "type": (sr_item.get("step") or {}).get("type") or "",
                    "selector": (sr_item.get("step") or {}).get("selector") or sr_item.get("details", {}).get("selector") if isinstance(sr_item.get("details"), dict) else None,
                    "status": sr_item.get("status") or "pending",
                    "duration_ms": sr_item.get("duration_ms"),
                    "error": sr_item.get("details", {}).get("error") if isinstance(sr_item.get("details"), dict) else (sr_item.get("details") if sr_item.get("status") == "failed" else None),
                    "screenshot": shot,
                })
            fh = t.fix_history or []
            ai_fix = None
            if fh:
                latest = fh[-1]
                ai_fix = {"analysis": latest.get("analysis", ""), "fixed_steps": latest.get("fixed_steps", []), "changes": latest.get("changes", [])}
            last_failed_run = {
                "id": r.id, "error_message": r.error_message,
                "failure_category": getattr(r, "failure_category", "") or "",
                "step_results": step_detail,
                "ai_fix": ai_fix,
                "platform": r.platform,
                "started_at": r.started_at.isoformat() if r.started_at else None,
            }
            break

    run_history = [{"id": r.id, "status": r.status, "platform": r.platform} for r in test_runs[-14:]]

    return {
        "id": t.id, "name": t.name, "status": status,
        "acceptance_criteria": t.acceptance_criteria or "",
        "steps_ran": steps_ran, "steps_total": steps_total,
        "platform": plat, "pass_rate_pct": rate, "fail_streak": streak,
        "last_passed_at": last_passed,
        "ai_fixes_count": len(t.fix_history or []),
        "run_history": run_history,
        "last_failed_run": last_failed_run,
    }


@app.get("/api/suites/{suite_id}/health")
def suite_health(suite_id: int, days: int = 14, platform: str = "") -> dict[str, Any]:
    with SessionLocal() as db:
        suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
        if not suite:
            raise HTTPException(status_code=404, detail="Suite not found")
        module = db.query(Module).filter(Module.id == suite.module_id).first()
        tests = db.query(TestDefinition).filter(TestDefinition.suite_id == suite_id).all()
        test_ids = [t.id for t in tests]
        prereq_ids = set(t.prerequisite_test_id for t in tests if t.prerequisite_test_id and t.prerequisite_test_id != t.id)
        prereq_map: dict = {}
        if prereq_ids:
            for p in db.query(TestDefinition).filter(TestDefinition.id.in_(prereq_ids)).all():
                prereq_map[p.id] = p
        runs = _run_window(db, test_ids, days, platform, by_suite=False) if test_ids else []
        runs_by_test: dict[int, list[Run]] = {}
        for r in runs:
            runs_by_test.setdefault(r.test_id, []).append(r)

        rows = [_test_health_row(t, runs_by_test.get(t.id, []), days, prereq_def=prereq_map.get(t.prerequisite_test_id)) for t in tests]
        # sort: failing first (by streak desc), flaky, passing, not_run
        order = {"failing": 0, "flaky": 1, "passing": 2, "not_run": 3}
        rows.sort(key=lambda r: (order.get(r["status"], 4), -r["fail_streak"], r["name"]))

        passing = sum(1 for r in rows if r["status"] == "passing")
        failing = sum(1 for r in rows if r["status"] == "failing")
        flaky = sum(1 for r in rows if r["status"] == "flaky")
        never_run = sum(1 for r in rows if r["status"] == "not_run")
        avg_steps = 0
        denom = sum(1 for r in rows if r["steps_total"] > 0)
        if denom:
            avg_steps = round(sum(r["steps_ran"] / r["steps_total"] * 100 for r in rows if r["steps_total"] > 0) / denom)

        last_run_at = None
        if runs:
            lr = max(runs, key=lambda r: r.finished_at or r.started_at or datetime.min)
            last_run_at = (lr.finished_at or lr.started_at or datetime.min).isoformat()

        suite_rate = round(100 * passing / len(rows)) if rows else 0

    return {
        "suite": {"id": suite.id, "name": suite.name, "module_name": module.name if module else "", "last_run_at": last_run_at, "pass_rate": suite_rate},
        "metrics": {"total": len(rows), "passing": passing, "failing": failing, "flaky": flaky, "never_run": never_run, "avg_steps_pct": avg_steps},
        "tests": rows,
    }


@app.get("/api/suites/{suite_id}/trend")
def suite_trend(suite_id: int, days: int = 14, platform: str = "") -> list[dict[str, Any]]:
    with SessionLocal() as db:
        tests = db.query(TestDefinition).filter(TestDefinition.suite_id == suite_id).all()
        test_ids = [t.id for t in tests]
        runs = _run_window(db, test_ids, days, platform, by_suite=False) if test_ids else []
        runs_by_test: dict[int, list[Run]] = {}
        for r in runs:
            runs_by_test.setdefault(r.test_id, []).append(r)
        out = []
        for t in tests:
            trs = runs_by_test.get(t.id, [])
            total = len(trs)
            passed = sum(1 for r in trs if r.status == "passed")
            out.append({
                "test_case_id": t.id, "test_name": t.name,
                "pass_count": passed, "total_runs": total,
                "pass_rate_pct": round(100 * passed / total) if total else 0,
            })
    return out


@app.get("/api/suites/{suite_id}/step-coverage")
def suite_step_coverage(suite_id: int, days: int = 14, platform: str = "") -> list[dict[str, Any]]:
    with SessionLocal() as db:
        tests = db.query(TestDefinition).filter(TestDefinition.suite_id == suite_id).all()
        test_ids = [t.id for t in tests]
        prereq_ids = set(t.prerequisite_test_id for t in tests if t.prerequisite_test_id and t.prerequisite_test_id != t.id)
        prereq_map: dict = {}
        if prereq_ids:
            for p in db.query(TestDefinition).filter(TestDefinition.id.in_(prereq_ids)).all():
                prereq_map[p.id] = p
        runs = _run_window(db, test_ids, days, platform, by_suite=False) if test_ids else []
        runs_by_test: dict[int, list[Run]] = {}
        for r in runs:
            runs_by_test.setdefault(r.test_id, []).append(r)
        out = []
        for t in tests:
            trs = runs_by_test.get(t.id, [])
            if not trs:
                total_steps = max(len(_steps_for_platform_record(t, "android")), len(_steps_for_platform_record(t, "ios_sim")))
                out.append({"test_case_id": t.id, "test_name": t.name, "avg_steps_ran": 0, "avg_steps_total": total_steps, "coverage_pct": 0})
                continue
            defined_total = max(len(_steps_for_platform_record(t, "android")), len(_steps_for_platform_record(t, "ios_sim")))
            prereq_def = prereq_map.get(t.prerequisite_test_id)
            ran_sum = 0
            for r in trs:
                sr = r.summary or {}
                step_results = sr.get("stepResults") or []
                all_ran = sum(1 for s in step_results if s.get("status") in ("passed", "failed"))
                r_pf = (r.platform or "android").strip() or "android"
                r_offset = len(_steps_for_platform_record(prereq_def, r_pf)) if prereq_def else 0
                ran_sum += max(0, all_ran - r_offset)
            n = len(trs)
            total_sum = defined_total * n
            avg_ran = round(ran_sum / n, 1)
            avg_total = float(defined_total)
            cov = round(100 * ran_sum / total_sum) if total_sum else 0
            out.append({"test_case_id": t.id, "test_name": t.name, "avg_steps_ran": avg_ran, "avg_steps_total": avg_total, "coverage_pct": cov})
    return out


@app.get("/api/suites/{suite_id}/triage")
def suite_triage(suite_id: int, days: int = 14, platform: str = "") -> dict[str, Any]:
    with SessionLocal() as db:
        tests = db.query(TestDefinition).filter(TestDefinition.suite_id == suite_id).all()
        test_map = {t.id: t for t in tests}
        test_ids = [t.id for t in tests]
        runs = _run_window(db, test_ids, days, platform, by_suite=False) if test_ids else []
        failed_runs = [r for r in runs if r.status in ("failed", "error")]
        cat_map: dict[str, list[dict]] = {}
        for r in failed_runs:
            cat = getattr(r, "failure_category", "") or _classify_error(r.error_message or "")
            cat_map.setdefault(cat, [])
            t = test_map.get(r.test_id)
            cat_map[cat].append({"id": t.id if t else 0, "name": t.name if t else f"Run #{r.id}", "error_message": r.error_message or ""})
        total_failures = len(failed_runs)
        categories = []
        for cat in ["selector_not_found", "element_timeout", "assertion_failure", "network_error", "app_crash", "other"]:
            items = cat_map.get(cat, [])
            if not items and cat not in cat_map:
                continue
            categories.append({
                "category": cat,
                "count": len(items),
                "pct": round(100 * len(items) / total_failures) if total_failures else 0,
                "affected_tests": items,
            })
    return {"categories": categories, "total_failures": total_failures}


@app.get("/api/collections/{collection_id}/health")
def collection_health(collection_id: int, days: int = 14, platform: str = "") -> dict[str, Any]:
    with SessionLocal() as db:
        module = db.query(Module).filter(Module.id == collection_id).first()
        if not module:
            raise HTTPException(status_code=404, detail="Collection not found")
        suites = db.query(TestSuite).filter(TestSuite.module_id == collection_id).all()
        all_tests = db.query(TestDefinition).filter(TestDefinition.suite_id.in_([s.id for s in suites])).all() if suites else []
        test_ids = [t.id for t in all_tests]
        prereq_ids = set(t.prerequisite_test_id for t in all_tests if t.prerequisite_test_id and t.prerequisite_test_id != t.id)
        prereq_map: dict = {}
        if prereq_ids:
            for p in db.query(TestDefinition).filter(TestDefinition.id.in_(prereq_ids)).all():
                prereq_map[p.id] = p
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        q = db.query(Run).filter(Run.started_at >= cutoff, Run.test_id.in_(test_ids)) if test_ids else None
        if platform and q is not None:
            q = q.filter(Run.platform == platform)
        runs = q.order_by(Run.id.asc()).all() if q is not None else []
        runs_by_test: dict[int, list[Run]] = {}
        for r in runs:
            runs_by_test.setdefault(r.test_id, []).append(r)

        # per-suite rollup
        suite_rows = []
        all_health = []
        for s in suites:
            s_tests = [t for t in all_tests if t.suite_id == s.id]
            s_rows = [_test_health_row(t, runs_by_test.get(t.id, []), days, prereq_def=prereq_map.get(t.prerequisite_test_id)) for t in s_tests]
            all_health.extend(s_rows)
            s_pass = sum(1 for r in s_rows if r["status"] == "passing")
            s_fail = sum(1 for r in s_rows if r["status"] == "failing")
            s_blocker = sum(1 for r in s_rows if r["fail_streak"] >= 5 or (r.get("last_failed_run") or {}).get("failure_category") == "app_crash")
            s_runs = [r for r in runs if r.test_id in [t.id for t in s_tests]]
            last_at = None
            if s_runs:
                lr = max(s_runs, key=lambda r: r.finished_at or r.started_at or datetime.min)
                last_at = (lr.finished_at or lr.started_at).isoformat() if (lr.finished_at or lr.started_at) else None
            rate = round(100 * s_pass / len(s_rows)) if s_rows else 0
            suite_rows.append({"id": s.id, "name": s.name, "pass_rate_pct": rate, "pass_count": s_pass, "fail_count": s_fail, "blocker_count": s_blocker, "last_run_at": last_at, "total": len(s_rows)})

        total = len(all_health)
        passing = sum(1 for h in all_health if h["status"] == "passing")
        failing = sum(1 for h in all_health if h["status"] == "failing")
        flaky = sum(1 for h in all_health if h["status"] == "flaky")
        never_run = sum(1 for h in all_health if h["status"] == "not_run")
        blockers = sum(1 for h in all_health if h["fail_streak"] >= 5 or (h.get("last_failed_run") or {}).get("failure_category") == "app_crash")
        rate = round(100 * passing / total) if total else 0

        # verdict
        if blockers > 0:
            verdict = "BLOCKED"
        elif rate < 90:
            verdict = "NOT_READY"
        else:
            verdict = "READY"

        # 30-day trend
        from datetime import timedelta as td
        cutoff_30 = datetime.utcnow() - td(days=30)
        trend_runs = [r for r in runs if r.started_at and r.started_at >= cutoff_30]
        day_buckets: dict[str, dict] = {}
        for r in trend_runs:
            day = r.started_at.strftime("%Y-%m-%d") if r.started_at else "?"
            day_buckets.setdefault(day, {"passed": 0, "total": 0})
            day_buckets[day]["total"] += 1
            if r.status == "passed":
                day_buckets[day]["passed"] += 1
        trend = [{"date": d, "pass_rate_pct": round(100 * v["passed"] / v["total"]) if v["total"] else 0} for d, v in sorted(day_buckets.items())]

    return {
        "collection": {"id": module.id, "name": module.name, "pass_rate": rate, "verdict": verdict},
        "metrics": {"total": total, "passing": passing, "failing": failing, "blockers": blockers, "flaky": flaky, "never_run": never_run},
        "suites": suite_rows,
        "trend_30d": trend,
    }


@app.get("/api/collections/{collection_id}/blockers")
def collection_blockers(collection_id: int, days: int = 14, platform: str = "") -> list[dict[str, Any]]:
    with SessionLocal() as db:
        suites = db.query(TestSuite).filter(TestSuite.module_id == collection_id).all()
        suite_map = {s.id: s.name for s in suites}
        all_tests = db.query(TestDefinition).filter(TestDefinition.suite_id.in_([s.id for s in suites])).all() if suites else []
        test_ids = [t.id for t in all_tests]
        prereq_ids = set(t.prerequisite_test_id for t in all_tests if t.prerequisite_test_id and t.prerequisite_test_id != t.id)
        prereq_map: dict = {}
        if prereq_ids:
            for p in db.query(TestDefinition).filter(TestDefinition.id.in_(prereq_ids)).all():
                prereq_map[p.id] = p
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        q = db.query(Run).filter(Run.started_at >= cutoff, Run.test_id.in_(test_ids)) if test_ids else None
        if platform and q is not None:
            q = q.filter(Run.platform == platform)
        runs = q.order_by(Run.id.asc()).all() if q is not None else []
        runs_by_test: dict[int, list[Run]] = {}
        for r in runs:
            runs_by_test.setdefault(r.test_id, []).append(r)

        out = []
        for t in all_tests:
            trs = runs_by_test.get(t.id, [])
            if not trs:
                continue
            h = _test_health_row(t, trs, days, prereq_def=prereq_map.get(t.prerequisite_test_id))
            is_blocker = h["fail_streak"] >= 5 or (h.get("last_failed_run") or {}).get("failure_category") == "app_crash"
            if not is_blocker:
                continue
            lfr = h.get("last_failed_run") or {}
            screenshots = lfr.get("step_results") or []
            shot = None
            for sr in reversed(screenshots):
                if sr.get("screenshot"):
                    shot = sr["screenshot"]
                    break
            out.append({
                "test_id": t.id, "test_name": t.name,
                "suite_name": suite_map.get(t.suite_id, ""),
                "error_message": lfr.get("error_message") or "",
                "fail_streak": h["fail_streak"],
                "run_id": lfr.get("id") or 0,
                "screenshot_path": shot,
                "ai_fix_available": bool(t.fix_history),
            })
    return out


# ── Report export endpoints ────────────────────────────────────────────

_REPORT_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter','Segoe UI',system-ui,sans-serif;background:#080b0f;color:#e8eaed;padding:32px 40px;line-height:1.6;max-width:1100px;margin:0 auto}
h1{font-size:22px;color:#fff;margin-bottom:2px;font-weight:700}
h2{font-size:15px;color:#a78bfa;margin:32px 0 14px;border-bottom:1px solid #1e2430;padding-bottom:8px;font-weight:600}
.subtitle{font-size:12px;color:#6b7280;margin-bottom:24px}
.stats{display:flex;gap:16px;margin-bottom:28px;flex-wrap:wrap}
.stat{background:#111820;border:1px solid #1e2430;border-radius:10px;padding:14px 20px;min-width:100px;text-align:center}
.stat-val{font-size:22px;font-weight:700;letter-spacing:-.5px}
.stat-lbl{font-size:9px;text-transform:uppercase;color:#6b7280;margin-top:3px;letter-spacing:.8px}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-weight:600;font-size:11px}
.badge-pass{background:#0d2818;color:#00e5a0;border:1px solid #0f3d22}
.badge-fail{background:#2a0a10;color:#ff3b5c;border:1px solid #3d1118}
.badge-flaky{background:#2a1f06;color:#ffb020;border:1px solid #3d2c0a}
.badge-skip{background:#1a1a1a;color:#6b7280;border:1px solid #2a2a2a}
table{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:24px}
thead th{background:#0f1318;color:#6b7280;text-transform:uppercase;font-size:9.5px;letter-spacing:.6px;text-align:left;padding:10px 12px;border-bottom:2px solid #1e2430;font-weight:600}
tbody td{padding:10px 12px;border-bottom:1px solid #151a22;vertical-align:top}
tbody tr:hover{background:rgba(139,92,246,.03)}
.step-row{display:grid;grid-template-columns:32px 10px 1fr 70px 60px;gap:8px;align-items:center;padding:7px 10px;border-radius:6px;font-size:12px;min-height:40px}
.step-row:nth-child(even){background:rgba(255,255,255,.015)}
.step-row--fail{background:rgba(255,59,92,.04);border-left:3px solid #ff3b5c}
.step-num{text-align:right;color:#555;font-size:11px;font-weight:600;font-variant-numeric:tabular-nums}
.dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.selector{color:#6b7280;font-size:10.5px;font-family:'SF Mono','Fira Code',monospace}
.dur{color:#555;font-size:10.5px;text-align:right;font-variant-numeric:tabular-nums}
.screenshot-thumb{width:48px;border-radius:4px;border:1px solid #1e2430;cursor:pointer}
.screenshot-thumb--fail{border-color:#ff3b5c}
.error-box{background:rgba(255,59,92,.06);border:1px solid rgba(255,59,92,.15);padding:14px 16px;border-radius:8px;margin:12px 0;font-size:12px;color:#ff7b93;line-height:1.5}
.fix-box{background:rgba(139,92,246,.06);border:1px solid rgba(139,92,246,.15);padding:14px 16px;border-radius:8px;margin:12px 0}
.fix-label{font-weight:700;color:#a78bfa;margin-bottom:6px;text-transform:uppercase;font-size:9.5px;letter-spacing:.8px}
.fix-text{font-size:12px;color:#c4b5fd;line-height:1.5}
.meta-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-bottom:24px}
.meta-item{font-size:11px;color:#6b7280}.meta-item strong{color:#c9d1d9}
.history-strip{display:flex;gap:2px;flex-wrap:wrap}
.history-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.footer{margin-top:40px;border-top:1px solid #1e2430;padding-top:16px;font-size:10px;color:#3d4250}
.section{margin-bottom:28px}
img.screenshot-full{max-width:300px;border-radius:8px;border:1px solid #1e2430;margin-top:8px}
"""


import html as _html


def _esc(text: str) -> str:
    """HTML-escape text for safe embedding in reports."""
    return _html.escape(str(text)) if text else ""


def _screenshot_to_base64(project_id: int, run_id: int, filename: str) -> str:
    """Read a screenshot from disk and return as base64 data URI."""
    import base64
    fpath = settings.artifacts_dir / str(project_id) / str(run_id) / filename
    if not fpath.exists():
        return ""
    try:
        data = fpath.read_bytes()
        return f"data:image/png;base64,{base64.b64encode(data).decode()}"
    except Exception:
        return ""


def _render_step_rows_html(step_results: list, project_id: int, run_id: int) -> str:
    """Render step results as HTML rows with embedded screenshots."""
    rows = ""
    for sr in step_results:
        idx = sr.get("index", 0)
        st = sr.get("status", "pending")
        stype = _esc(sr.get("type") or "")
        sel = sr.get("selector")
        sel_str = ""
        if sel:
            if isinstance(sel, dict):
                sel_str = f'{_esc(sel.get("using", ""))}=&quot;{_esc(sel.get("value", ""))}&quot;'
            else:
                sel_str = _esc(str(sel))
        dur = sr.get("duration_ms")
        dur_str = f'{dur/1000:.1f}s' if dur is not None else ""
        shot = sr.get("screenshot", "")
        shot_html = ""
        if shot:
            b64 = _screenshot_to_base64(project_id, run_id, shot)
            if b64:
                fail_cls = " screenshot-thumb--fail" if st == "failed" else ""
                shot_html = f'<img src="{b64}" class="screenshot-thumb{fail_cls}" />'
        dot_color = "#00e5a0" if st == "passed" else "#ff3b5c" if st == "failed" else "#555"
        fail_cls = " step-row--fail" if st == "failed" else ""
        error_detail = ""
        if st == "failed" and sr.get("error"):
            err = sr["error"] if isinstance(sr["error"], str) else str(sr["error"])
            error_detail = f'<div style="grid-column:3/6;font-size:11px;color:#ff7b93;margin-top:2px">{_esc(err)}</div>'
        rows += f'''<div class="step-row{fail_cls}">
<div class="step-num">{idx + 1}</div>
<div class="dot" style="background:{dot_color}"></div>
<div><strong>{stype}</strong>{f' <span class="selector">{sel_str}</span>' if sel_str else ""}{" — <em style='color:#555'>skipped</em>" if st not in ("passed","failed") else ""}</div>
<div class="dur">{dur_str}</div>
<div>{shot_html}</div>
{error_detail}
</div>'''
    return rows


@app.get("/api/tests/{test_id}/export/html")
def export_test_html(test_id: int, days: int = 14) -> StreamingResponse:
    """Generate a rich self-contained HTML report for a single test case."""
    with SessionLocal() as db:
        test = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not test:
            raise HTTPException(status_code=404, detail="Test not found")
        project = db.query(Project).filter(Project.id == test.project_id).first()
        runs = db.query(Run).filter(Run.test_id == test_id).order_by(Run.id.desc()).limit(50).all()

        # Build info from latest run
        build_info = ""
        latest_run = runs[0] if runs else None
        if latest_run and latest_run.build_id:
            build = db.query(Build).filter(Build.id == latest_run.build_id).first()
            if build:
                build_info = f"{build.file_name} ({build.platform})"

        passed = sum(1 for r in runs if r.status == "passed")
        total = len(runs)
        rate = round(100 * passed / total) if total else 0
        streak = 0
        for r in runs:
            if r.status in ("failed", "error"):
                streak += 1
            else:
                break

        last_passed_at = None
        for r in runs:
            if r.status == "passed":
                last_passed_at = r.finished_at.isoformat() if r.finished_at else None
                break

        # Get the last failed run with full detail
        last_failed = None
        for r in runs:
            if r.status in ("failed", "error"):
                last_failed = r
                break

        prereq_def = None
        if test.prerequisite_test_id and test.prerequisite_test_id != test.id:
            prereq_def = db.query(TestDefinition).filter(TestDefinition.id == test.prerequisite_test_id).first()
        step_html = ""
        error_html = ""
        fix_html = ""
        fail_screenshot_html = ""
        if last_failed:
            rsummary = last_failed.summary or {}
            rsteps = rsummary.get("stepResults") or []
            arts = last_failed.artifacts or {}
            screenshots = arts.get("screenshots") or []
            # Skip prerequisite steps using the run's actual platform
            run_pf = (last_failed.platform or "android").strip() or "android"
            poffset = len(_steps_for_platform_record(prereq_def, run_pf)) if prereq_def else 0
            own_rsteps = rsteps[poffset:]
            own_screenshots = screenshots[poffset:]
            step_detail = []
            for si, sr_item in enumerate(own_rsteps):
                shot = own_screenshots[si] if si < len(own_screenshots) else None
                step_detail.append({
                    "index": si,
                    "type": (sr_item.get("step") or {}).get("type") or "",
                    "selector": (sr_item.get("step") or {}).get("selector") or (sr_item.get("details", {}).get("selector") if isinstance(sr_item.get("details"), dict) else None),
                    "status": sr_item.get("status") or "pending",
                    "duration_ms": sr_item.get("duration_ms"),
                    "error": sr_item.get("details", {}).get("error") if isinstance(sr_item.get("details"), dict) else (sr_item.get("details") if sr_item.get("status") == "failed" else None),
                    "screenshot": shot,
                })

            step_html = _render_step_rows_html(step_detail, test.project_id, last_failed.id)

            if last_failed.error_message:
                error_html = f'<div class="error-box">{_esc(last_failed.error_message)}</div>'

            # Failed step screenshot (full size)
            for sd in step_detail:
                if sd["status"] == "failed" and sd.get("screenshot"):
                    b64 = _screenshot_to_base64(test.project_id, last_failed.id, sd["screenshot"])
                    if b64:
                        fail_screenshot_html = f'<h2>Failure Screenshot (Step {sd["index"]+1})</h2><img class="screenshot-full" src="{b64}" />'
                    break

            fh = test.fix_history or []
            if fh:
                latest_fix = fh[-1]
                fix_html = f'<div class="fix-box"><div class="fix-label">AI Fix Suggestion</div><div class="fix-text">{_esc(latest_fix.get("analysis",""))}</div></div>'

        # Run history strip
        history_html = ""
        for r in reversed(runs[:30]):
            c = "#00e5a0" if r.status == "passed" else "#ff3b5c" if r.status in ("failed","error") else "#555"
            ts = r.finished_at.strftime("%b %d %H:%M") if r.finished_at else ""
            history_html += f'<div class="history-dot" style="background:{c}" title="Run #{r.id} {r.status} {ts}"></div>'

        status_badge = "badge-pass" if streak == 0 and passed > 0 else "badge-fail" if streak > 0 else "badge-skip"
        status_label = "Passing" if streak == 0 and passed > 0 else "Failing" if streak > 0 else "No runs"

        steps_total = max(len(_steps_for_platform_record(test, "android")), len(_steps_for_platform_record(test, "ios_sim")))
        steps_ran = 0
        if last_failed:
            sr_list = (last_failed.summary or {}).get("stepResults") or []
            all_ran = sum(1 for s in sr_list if s.get("status") in ("passed", "failed"))
            steps_ran = max(0, all_ran - poffset)
        elif latest_run:
            sr_list = (latest_run.summary or {}).get("stepResults") or []
            all_ran = sum(1 for s in sr_list if s.get("status") in ("passed", "failed"))
            run_pf = (latest_run.platform or "android").strip() or "android"
            lr_offset = len(_steps_for_platform_record(prereq_def, run_pf)) if prereq_def else 0
            steps_ran = max(0, all_ran - lr_offset)

        dur_s = None
        if last_failed and last_failed.finished_at and last_failed.started_at:
            dur_s = round((last_failed.finished_at - last_failed.started_at).total_seconds(), 1)

        html = f'''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>{test.name} — Test Report</title>
<style>{_REPORT_CSS}</style></head><body>
<h1>{test.name} <span class="badge {status_badge}">{status_label}</span></h1>
<div class="subtitle">{project.name if project else ""} · Generated {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC</div>

<div class="stats">
<div class="stat"><div class="stat-val" style="color:{"#00e5a0" if rate>=80 else "#ffb020" if rate>=50 else "#ff3b5c"}">{rate}%</div><div class="stat-lbl">Pass Rate</div></div>
<div class="stat"><div class="stat-val">{total}</div><div class="stat-lbl">Total Runs</div></div>
<div class="stat"><div class="stat-val" style="color:#00e5a0">{passed}</div><div class="stat-lbl">Passed</div></div>
<div class="stat"><div class="stat-val" style="color:#ff3b5c">{total - passed}</div><div class="stat-lbl">Failed</div></div>
<div class="stat"><div class="stat-val" style="color:{"#ff3b5c" if streak > 3 else "#ffb020" if streak > 0 else "#00e5a0"}">{streak}</div><div class="stat-lbl">Fail Streak</div></div>
<div class="stat"><div class="stat-val">{steps_ran}/{steps_total}</div><div class="stat-lbl">Steps Ran</div></div>
</div>

<div class="meta-grid">
<div class="meta-item">Build: <strong>{build_info or "—"}</strong></div>
<div class="meta-item">Platform: <strong>{last_failed.platform if last_failed else "—"}</strong></div>
<div class="meta-item">Device: <strong>{last_failed.device_target if last_failed else "—"}</strong></div>
<div class="meta-item">Last passed: <strong>{last_passed_at[:10] if last_passed_at else "Never"}</strong></div>
<div class="meta-item">Last run: <strong>{latest_run.finished_at.strftime("%Y-%m-%d %H:%M") if latest_run and latest_run.finished_at else "—"}</strong></div>
<div class="meta-item">Duration: <strong>{f"{dur_s}s" if dur_s else "—"}</strong></div>
<div class="meta-item">AI fixes used: <strong>{len(test.fix_history or [])}</strong></div>
<div class="meta-item">Failure category: <strong>{last_failed.failure_category if last_failed and hasattr(last_failed, "failure_category") else "—"}</strong></div>
</div>

{f'<div class="section"><div style="font-size:11px;color:#6b7280;margin-bottom:6px">ACCEPTANCE CRITERIA</div><div style="font-size:12px;color:#c9d1d9;padding:10px 14px;background:#111820;border-radius:6px;border:1px solid #1e2430">{_esc(test.acceptance_criteria)}</div></div>' if test.acceptance_criteria else ""}

<h2>Run History (last {min(len(runs), 30)} runs)</h2>
<div class="history-strip" style="margin-bottom:24px">{history_html}</div>

{f'<h2>Step Execution — Run #{last_failed.id}</h2>{step_html}' if step_html else '<div style="color:#6b7280;padding:20px">No failed run to display steps for.</div>'}

{error_html}
{fix_html}
{fail_screenshot_html}

<div class="footer">QA·OS Test Case Report · {test.name} · {datetime.utcnow().isoformat()}</div>
</body></html>'''

    safe_name = test.name.replace(" ", "_").replace("/", "_")[:60]
    return StreamingResponse(
        iter([html]), media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_report.html"'},
    )


@app.get("/api/suites/{suite_id}/export/csv")
def export_suite_csv(suite_id: int, days: int = 14, platform: str = ""):
    import csv as csvmod
    import io
    data = suite_health(suite_id, days, platform)
    buf = io.StringIO()
    w = csvmod.writer(buf)
    w.writerow(["test_name", "status", "pass_rate_pct", "steps_ran", "steps_total", "fail_streak", "last_passed", "error_message", "failure_category", "platform"])
    for t in data["tests"]:
        lfr = t.get("last_failed_run") or {}
        w.writerow([
            t["name"], t["status"], t["pass_rate_pct"],
            t["steps_ran"], t["steps_total"], t["fail_streak"],
            t.get("last_passed_at") or "", lfr.get("error_message") or "",
            lfr.get("failure_category") or "", t["platform"],
        ])
    buf.seek(0)
    sname = data["suite"]["name"].replace(" ", "_")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="suite_{sname}_health.csv"'},
    )


@app.get("/api/suites/{suite_id}/export/html")
def export_suite_html(suite_id: int, days: int = 14, platform: str = ""):
    data = suite_health(suite_id, days, platform)
    s = data["suite"]
    m = data["metrics"]
    tests = data["tests"]

    # Find project_id for screenshot embedding
    with SessionLocal() as db:
        suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
        mod = db.query(Module).filter(Module.id == suite.module_id).first() if suite else None
        project_id = mod.project_id if mod else 0

    def _badge(st):
        if st == "passing": return '<span class="badge badge-pass">Passing</span>'
        if st == "failing": return '<span class="badge badge-fail">Failing</span>'
        if st == "flaky": return '<span class="badge badge-flaky">Flaky</span>'
        return '<span class="badge badge-skip">No runs</span>'

    test_sections = ""
    for t in tests:
        strip = "".join(f'<div class="history-dot" style="background:{"#00e5a0" if rh["status"]=="passed" else "#ff3b5c" if rh["status"] in ("failed","error") else "#555"}" title="#{rh["id"]} {rh["status"]}"></div>' for rh in t["run_history"])
        rate_c = "#00e5a0" if t["pass_rate_pct"] >= 80 else "#ffb020" if t["pass_rate_pct"] >= 50 else "#ff3b5c"

        detail_html = ""
        if t.get("last_failed_run"):
            lfr = t["last_failed_run"]
            step_html = _render_step_rows_html(lfr.get("step_results", []), project_id, lfr["id"])
            err = f'<div class="error-box">{_esc(lfr["error_message"])}</div>' if lfr.get("error_message") else ""
            fix = ""
            if lfr.get("ai_fix"):
                fix = f'<div class="fix-box"><div class="fix-label">AI Fix Suggestion</div><div class="fix-text">{_esc(lfr["ai_fix"].get("analysis",""))}</div></div>'
            detail_html = f'{step_html}{err}{fix}'

        test_sections += f'''
<div style="background:#0d1117;border:1px solid #1e2430;border-radius:10px;padding:16px 20px;margin-bottom:16px">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
<div style="font-size:14px;font-weight:600">{t["name"]} {_badge(t["status"])}</div>
<div style="display:flex;gap:16px;font-size:11px;color:#6b7280">
<span>Steps: <strong style="color:#e8eaed">{t["steps_ran"]}/{t["steps_total"]}</strong></span>
<span>Pass: <strong style="color:{rate_c}">{t["pass_rate_pct"]}%</strong></span>
<span>Streak: <strong style="color:{"#ff3b5c" if t["fail_streak"]>0 else "#e8eaed"}">{t["fail_streak"]}</strong></span>
</div>
</div>
<div class="history-strip" style="margin-bottom:10px">{strip}</div>
{detail_html}
</div>'''

    html = f'''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Suite Report — {s["name"]}</title>
<style>{_REPORT_CSS}</style></head><body>
<h1>Suite Report — {s["name"]}</h1>
<div class="subtitle">{s.get("module_name","")} · {days}-day window · Platform: {platform or "All"} · Generated {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC</div>
<div class="stats">
<div class="stat"><div class="stat-val" style="color:{"#00e5a0" if s["pass_rate"]>=80 else "#ffb020" if s["pass_rate"]>=50 else "#ff3b5c"}">{s["pass_rate"]}%</div><div class="stat-lbl">Pass Rate</div></div>
<div class="stat"><div class="stat-val">{m["total"]}</div><div class="stat-lbl">Total</div></div>
<div class="stat"><div class="stat-val" style="color:#00e5a0">{m["passing"]}</div><div class="stat-lbl">Passing</div></div>
<div class="stat"><div class="stat-val" style="color:#ff3b5c">{m["failing"]}</div><div class="stat-lbl">Failing</div></div>
<div class="stat"><div class="stat-val" style="color:#ffb020">{m["flaky"]}</div><div class="stat-lbl">Flaky</div></div>
<div class="stat"><div class="stat-val">{m["avg_steps_pct"]}%</div><div class="stat-lbl">Avg Steps</div></div>
</div>
<h2>Test Cases ({m["total"]})</h2>
{test_sections}
<div class="footer">QA·OS Suite Report · {s["name"]} · {datetime.utcnow().isoformat()}</div>
</body></html>'''

    return StreamingResponse(
        iter([html]), media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="suite_{s["name"].replace(" ","_")}_report.html"'},
    )


@app.get("/api/suites/{suite_id}/export/screenshots")
def export_suite_screenshots(suite_id: int, days: int = 14, platform: str = ""):
    import zipfile as zf
    data = suite_health(suite_id, days, platform)
    buf = BytesIO()
    total_size = 0
    MAX_SIZE = 15 * 1024 * 1024
    with zf.ZipFile(buf, "w", zf.ZIP_DEFLATED) as z:
        for t in data["tests"]:
            lfr = t.get("last_failed_run")
            if not lfr:
                continue
            for sr in lfr.get("step_results") or []:
                shot = sr.get("screenshot")
                if not shot:
                    continue
                # find the file on disk
                with SessionLocal() as db:
                    suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
                    if not suite:
                        continue
                    mod = db.query(Module).filter(Module.id == suite.module_id).first()
                    if not mod:
                        continue
                    project_id = mod.project_id
                art_path = settings.artifacts_dir / str(project_id) / str(lfr["id"]) / shot
                if art_path.exists():
                    content = art_path.read_bytes()
                    total_size += len(content)
                    if total_size > MAX_SIZE:
                        break
                    arcname = f'{data["suite"]["name"]}/{t["name"]}/run_{lfr["id"]}_step_{sr["index"]}.png'
                    z.writestr(arcname, content)
            if total_size > MAX_SIZE:
                break
    buf.seek(0)
    sname = data["suite"]["name"].replace(" ", "_")
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{sname}_screenshots.zip"'})


def _format_date(iso_str) -> str:
    """Format an ISO date string for reports, or return '—' for None/empty."""
    if not iso_str or iso_str == "None":
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %H:%M")
    except Exception:
        return str(iso_str)[:16]


@app.get("/api/collections/{collection_id}/export/html")
def export_collection_html(collection_id: int, days: int = 14, platform: str = ""):
    data = collection_health(collection_id, days, platform)
    c = data["collection"]
    m = data["metrics"]

    verdict_map = {"READY": "badge-pass", "BLOCKED": "badge-fail", "AT RISK": "badge-flaky"}
    verdict_cls = verdict_map.get(c["verdict"], "badge-skip")

    suite_cards = ""
    for s in data["suites"]:
        bar_w = s["pass_rate_pct"]
        bar_c = "#00e5a0" if bar_w >= 80 else "#ffb020" if bar_w >= 50 else "#ff3b5c"
        last_run = _format_date(s.get("last_run_at"))
        suite_cards += f'''
<div style="background:#0d1117;border:1px solid #1e2430;border-radius:10px;padding:16px 20px;margin-bottom:12px">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
<div style="font-size:14px;font-weight:600">{_esc(s["name"])}</div>
<div style="font-size:11px;color:#6b7280">Last run: {last_run}</div>
</div>
<div style="display:flex;gap:20px;align-items:center;margin-bottom:8px">
<div style="flex:1;height:10px;background:#1e2430;border-radius:5px;overflow:hidden"><div style="height:100%;width:{bar_w}%;background:{bar_c};border-radius:5px"></div></div>
<div style="font-size:18px;font-weight:700;color:{bar_c};min-width:50px;text-align:right">{s["pass_rate_pct"]}%</div>
</div>
<div style="display:flex;gap:20px;font-size:11px;color:#6b7280">
<span>Pass: <strong style="color:#00e5a0">{s["pass_count"]}</strong></span>
<span>Fail: <strong style="color:#ff3b5c">{s["fail_count"]}</strong></span>
<span>Blockers: <strong style="color:#ff6b35">{s["blocker_count"]}</strong></span>
<span>Total: <strong style="color:#e8eaed">{s["pass_count"] + s["fail_count"]}</strong></span>
</div>
</div>'''

    # Build a proper trend chart with axes, gridlines, labels, and data points
    trend_html = ""
    pts = data.get("trend_30d") or []
    if len(pts) >= 2:
        w, h = 600, 160
        pad_l, pad_r, pad_t, pad_b = 45, 20, 10, 30
        cw = w - pad_l - pad_r
        ch = h - pad_t - pad_b
        # Y axis gridlines and labels (0%, 25%, 50%, 75%, 100%)
        grid_lines = ""
        for pct in [0, 25, 50, 75, 100]:
            y = pad_t + ch - (pct / 100 * ch)
            grid_lines += f'<line x1="{pad_l}" y1="{y}" x2="{w - pad_r}" y2="{y}" stroke="#1e2430" stroke-width="1"/>'
            grid_lines += f'<text x="{pad_l - 6}" y="{y + 3}" text-anchor="end" fill="#6b7280" font-size="9" font-family="system-ui">{pct}%</text>'
        # Data points and line
        coords = []
        dots = ""
        labels = ""
        for i, p in enumerate(pts):
            x = pad_l + (i * cw / max(len(pts) - 1, 1))
            y = pad_t + ch - (p["pass_rate_pct"] / 100 * ch)
            coords.append(f"{x},{y}")
            dot_c = "#00e5a0" if p["pass_rate_pct"] >= 50 else "#ff3b5c"
            dots += f'<circle cx="{x}" cy="{y}" r="4" fill="{dot_c}" stroke="#080b0f" stroke-width="2"/>'
            dots += f'<text x="{x}" y="{y - 10}" text-anchor="middle" fill="#e8eaed" font-size="9" font-weight="600" font-family="system-ui">{p["pass_rate_pct"]}%</text>'
            # X axis labels (show every Nth to avoid crowding)
            date_str = str(p.get("date", ""))
            if date_str and (i == 0 or i == len(pts) - 1 or len(pts) <= 7 or i % max(1, len(pts) // 5) == 0):
                labels += f'<text x="{x}" y="{h - 4}" text-anchor="middle" fill="#6b7280" font-size="8" font-family="system-ui">{date_str[5:]}</text>'
        poly = " ".join(coords)
        # Area fill under the line
        area_pts = f"{coords[0].split(',')[0]},{pad_t + ch} {poly} {coords[-1].split(',')[0]},{pad_t + ch}"
        trend_html = f'''<h2>30-Day Pass Rate Trend</h2>
<svg viewBox="0 0 {w} {h}" style="width:100%;max-width:700px;height:{h}px;margin-bottom:16px">
{grid_lines}
<polygon points="{area_pts}" fill="rgba(0,229,160,.06)"/>
<polyline points="{poly}" fill="none" stroke="#00e5a0" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
{dots}
{labels}
</svg>'''
    elif len(pts) == 1:
        trend_html = f'<h2>30-Day Trend</h2><div style="padding:16px;background:#0d1117;border:1px solid #1e2430;border-radius:8px;font-size:12px;color:#6b7280">Only 1 data point — {pts[0]["pass_rate_pct"]}% pass rate. More data points needed to render a trend.</div>'

    html = f'''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Collection Report — {_esc(c["name"])}</title>
<style>{_REPORT_CSS}</style></head><body>
<h1>{_esc(c["name"])} <span class="badge {verdict_cls}">{c["verdict"]}</span></h1>
<div class="subtitle">{days}-day window · Platform: {platform or "All"} · Generated {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC</div>
<div class="stats">
<div class="stat"><div class="stat-val">{m["total"]}</div><div class="stat-lbl">Total</div></div>
<div class="stat"><div class="stat-val" style="color:#00e5a0">{m["passing"]}</div><div class="stat-lbl">Passing</div></div>
<div class="stat"><div class="stat-val" style="color:#ff3b5c">{m["failing"]}</div><div class="stat-lbl">Failing</div></div>
<div class="stat"><div class="stat-val" style="color:#ff6b35">{m["blockers"]}</div><div class="stat-lbl">Blockers</div></div>
<div class="stat"><div class="stat-val" style="color:#ffb020">{m["flaky"]}</div><div class="stat-lbl">Flaky</div></div>
<div class="stat"><div class="stat-val">{m["never_run"]}</div><div class="stat-lbl">Never Ran</div></div>
</div>
<h2>Suites ({len(data["suites"])})</h2>
{suite_cards}
{trend_html}
<div class="footer">QA·OS Collection Report · {_esc(c["name"])} · {datetime.utcnow().isoformat()}</div>
</body></html>'''

    return StreamingResponse(
        iter([html]), media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="collection_{c["name"].replace(" ","_")}_report.html"'},
    )


@app.get("/api/projects/{project_id}/reports/hierarchy")
def reports_hierarchy(project_id: int) -> dict[str, Any]:
    """Collection → suite → test → recent runs for reports UI."""
    with SessionLocal() as db:
        modules = db.query(Module).filter(Module.project_id == project_id).all()
        mod_ids = [m.id for m in modules]
        all_suites = db.query(TestSuite).filter(TestSuite.module_id.in_(mod_ids)).all() if mod_ids else []
        all_tests = db.query(TestDefinition).filter(TestDefinition.project_id == project_id).all()
        all_runs = (
            db.query(Run)
            .filter(Run.project_id == project_id)
            .order_by(Run.id.desc())
            .limit(500)
            .all()
        )

    runs_by_test: dict[int, list[Run]] = {}
    for r in all_runs:
        if r.test_id is not None:
            runs_by_test.setdefault(r.test_id, []).append(r)

    def run_out(r: Run) -> dict[str, Any]:
        arts = r.artifacts or {}
        summary = r.summary or {}
        step_results = summary.get("stepResults", []) or []
        step_defs = summary.get("stepDefinitions", []) or []
        total_steps = len(step_defs) if step_defs else len(step_results)
        dur: float | None = None
        if r.finished_at and r.started_at:
            dur = round((r.finished_at - r.started_at).total_seconds(), 1)
        return {
            "id": r.id,
            "status": r.status,
            "platform": r.platform,
            "device_target": r.device_target,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_s": dur,
            "error_message": r.error_message,
            "screenshots": arts.get("screenshots", []),
            "video": arts.get("video"),
            "step_results": step_results,
            "passed_steps": sum(1 for s in step_results if s and s.get("status") == "passed"),
            "total_steps": total_steps,
        }

    def test_out(t: TestDefinition) -> dict[str, Any]:
        test_runs = runs_by_test.get(t.id, [])[:5]
        latest = test_runs[0] if test_runs else None
        n_and = len(_steps_for_platform_record(t, "android"))
        n_ios = len(_steps_for_platform_record(t, "ios_sim"))
        return {
            "id": t.id,
            "name": t.name,
            "steps_count": max(n_and, n_ios),
            "acceptance_criteria": t.acceptance_criteria,
            "latest_status": latest.status if latest else "not_run",
            "latest_run_id": latest.id if latest else None,
            "runs": [run_out(r) for r in test_runs],
        }

    def suite_out(s: TestSuite) -> dict[str, Any]:
        tests = [t for t in all_tests if t.suite_id == s.id]
        test_outs = [test_out(t) for t in tests]
        passed = sum(1 for t in test_outs if t["latest_status"] == "passed")
        run_count = sum(1 for t in test_outs if t["latest_status"] != "not_run")
        return {
            "id": s.id,
            "name": s.name,
            "tests": test_outs,
            "total_tests": len(tests),
            "passed_count": passed,
            "failed_count": sum(1 for t in test_outs if t["latest_status"] in ("failed", "error")),
            "not_run_count": len(tests) - run_count,
            "pass_rate": round(passed / run_count * 100) if run_count > 0 else 0,
        }

    def module_out(m: Module) -> dict[str, Any]:
        suites = [s for s in all_suites if s.module_id == m.id]
        suite_outs = [suite_out(s) for s in suites]
        total = sum(s["total_tests"] for s in suite_outs)
        passed = sum(s["passed_count"] for s in suite_outs)
        run = total - sum(s["not_run_count"] for s in suite_outs)
        return {
            "id": m.id,
            "name": m.name,
            "suites": suite_outs,
            "total_tests": total,
            "passed_count": passed,
            "failed_count": sum(s["failed_count"] for s in suite_outs),
            "not_run_count": sum(s["not_run_count"] for s in suite_outs),
            "pass_rate": round(passed / run * 100) if run > 0 else 0,
        }

    collections = [module_out(m) for m in modules]
    total = sum(c["total_tests"] for c in collections)
    passed = sum(c["passed_count"] for c in collections)
    run = total - sum(c["not_run_count"] for c in collections)

    return {
        "collections": collections,
        "summary": {
            "total_tests": total,
            "executed": run,
            "passed": passed,
            "failed": sum(c["failed_count"] for c in collections),
            "not_run": total - run,
            "pass_rate": round(passed / run * 100) if run > 0 else 0,
        },
    }


# ── WebSocket ─────────────────────────────────────────────────────────

@app.websocket("/ws/runs/{run_id}")
async def ws_run_events(websocket: WebSocket, run_id: int):
    if _extract_websocket_token(websocket) != _get_auth_token():
        await websocket.close(code=1008)
        return
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
