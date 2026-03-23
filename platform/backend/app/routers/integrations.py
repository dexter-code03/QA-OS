"""Connection tests (Appium, Confluence, AI), Figma components, Confluence sync, device listing."""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from ..db import SessionLocal
from ..helpers import ai_creds, load_settings
from ..models import Project, Run, TestDefinition, TestSuite
from ..runner.appium_service import ensure_appium_running
from ..settings import settings

router = APIRouter()

_figma_components_cache: dict[str, Any] = {"ts": 0.0, "names": []}


@router.get("/api/devices")
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


@router.post("/api/test-connection/appium")
async def test_appium() -> dict[str, Any]:
    s = load_settings()
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


@router.post("/api/test-connection/confluence")
async def test_confluence() -> dict[str, Any]:
    s = load_settings()
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


@router.get("/api/integrations/figma/components")
def list_figma_components() -> dict[str, Any]:
    s = load_settings()
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


@router.post("/api/projects/{project_id}/confluence/sync")
async def sync_project_to_confluence(project_id: int) -> dict[str, Any]:
    import html as html_lib
    from datetime import datetime

    s = load_settings()
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


@router.post("/api/test-connection/ai")
async def test_ai() -> dict[str, Any]:
    s = load_settings()
    api_key, model = ai_creds(s)
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
