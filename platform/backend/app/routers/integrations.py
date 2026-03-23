"""Connection tests (Appium, Confluence, AI), Figma components, Confluence sync, device listing."""
from __future__ import annotations

import json
import os
import subprocess
import time
from functools import lru_cache
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from ..db import SessionLocal
from ..helpers import ai_creds, load_settings, utcnow
from ..models import Project, Run, TestDefinition, TestSuite
from ..runner.appium_service import ensure_appium_running
from ..settings import settings

router = APIRouter()

_FIGMA_COMPONENTS_TTL_SEC = 300


def _figma_components_ttl_bucket() -> int:
    return int(time.time() // _FIGMA_COMPONENTS_TTL_SEC)


@lru_cache(maxsize=32)
def _cached_figma_component_names(token: str, file_key: str, _ttl_bucket: int) -> tuple[str, ...]:
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
    return tuple(names)


def _confluence_cql_string_literal(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _existing_confluence_sync_page_id(results: list[dict[str, Any]], title_prefix: str) -> str | None:
    matches: list[dict[str, Any]] = []
    for item in results:
        t = str(item.get("title") or "")
        if t.startswith(title_prefix):
            matches.append(item)
    if not matches:
        return None
    matches.sort(
        key=lambda x: int((x.get("version") or {}).get("number") or 0),
        reverse=True,
    )
    pid = str(matches[0].get("id") or "")
    return pid or None


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
    try:
        bucket = _figma_components_ttl_bucket()
        names = list(_cached_figma_component_names(token, file_key, bucket))
        return {"names": names}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Figma request failed: {e}") from e


@router.post("/api/projects/{project_id}/confluence/sync")
async def sync_project_to_confluence(project_id: int) -> dict[str, Any]:
    import html as html_lib

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
    title = f"QA-OS — {proj.name} — {utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
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

        title_prefix = f"QA-OS — {proj.name} —"
        title_fragment = f"QA-OS — {proj.name}"
        cql = (
            f"type = page AND space = {_confluence_cql_string_literal(space_key)} "
            f"AND title ~ {_confluence_cql_string_literal(title_fragment)}"
        )
        sr = await client.get(
            f"{base}/rest/api/content/search",
            params={"cql": cql, "limit": 25, "expand": "version"},
            headers=headers,
        )
        if sr.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Confluence search failed: HTTP {sr.status_code} {sr.text[:400]}",
            )
        search_results = sr.json().get("results") or []
        existing_id = _existing_confluence_sync_page_id(search_results, title_prefix)

        if existing_id:
            gr = await client.get(
                f"{base}/rest/api/content/{existing_id}",
                params={"expand": "version"},
                headers=headers,
            )
            if gr.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Confluence load page failed: HTTP {gr.status_code} {gr.text[:400]}",
                )
            cur = gr.json()
            ver_num = int((cur.get("version") or {}).get("number") or 1)
            update_payload: dict[str, Any] = {
                "id": existing_id,
                "type": "page",
                "title": title,
                "version": {"number": ver_num + 1},
                "body": {"storage": {"value": body_html, "representation": "storage"}},
            }
            ur = await client.put(
                f"{base}/rest/api/content/{existing_id}",
                json=update_payload,
                headers=headers,
            )
            if ur.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Confluence update page failed: HTTP {ur.status_code} {ur.text[:400]}",
                )
            data = ur.json()
        else:
            payload = {
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "body": {"storage": {"value": body_html, "representation": "storage"}},
            }
            r = await client.post(f"{base}/rest/api/content", json=payload, headers=headers)
            if r.status_code not in (200, 201):
                raise HTTPException(
                    status_code=502,
                    detail=f"Confluence create page failed: HTTP {r.status_code} {r.text[:400]}",
                )
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
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, headers={"x-goog-api-key": api_key})
            if r.status_code == 200:
                return {"ok": True, "message": f"Connected to {model}"}
            return {"ok": False, "message": f"API returned HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "message": f"Cannot reach AI API: {e}"}
