from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..compose_detection import is_compose_screen
from ..db import SessionLocal
from ..helpers import load_settings, screen_to_dict
from ..models import Build, ScreenFolder, ScreenLibrary
from ..runner.appium_service import ensure_appium_running
from ..runner.screen_capture_session import (
    evict_dead_session,
    get_slot_for_status,
    make_session_key,
    session_active_and_alive,
    set_session_driver,
    stop_session,
    with_session_driver,
)
from ..settings import ensure_dirs, settings
from ..swiftui_detection import is_swiftui_screen

router = APIRouter()


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
    from ..runner.session import SessionConfig, create_driver

    s = load_settings()
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


@router.post("/api/screens/session/start")
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

    s = load_settings()
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


@router.post("/api/screens/session/stop")
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


@router.get("/api/screens/session/status")
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


@router.post("/api/screens/capture")
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

    s = load_settings()
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
            return screen_to_dict(existing)

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
        return screen_to_dict(entry)


@router.get("/api/screen-folders")
def list_screen_folders(project_id: int) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        folders = db.query(ScreenFolder).filter(ScreenFolder.project_id == project_id).order_by(ScreenFolder.name).all()
        return [{"id": f.id, "project_id": f.project_id, "name": f.name,
                 "screen_count": db.query(ScreenLibrary).filter(ScreenLibrary.folder_id == f.id).count(),
                 "created_at": f.created_at.isoformat() if f.created_at else None} for f in folders]


@router.post("/api/screen-folders")
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


@router.delete("/api/screen-folders/{folder_id}")
def delete_screen_folder(folder_id: int):
    with SessionLocal() as db:
        f = db.query(ScreenFolder).filter(ScreenFolder.id == folder_id).first()
        if not f:
            raise HTTPException(status_code=404, detail="Folder not found")
        db.delete(f)
        db.commit()
    return {"ok": True}


@router.get("/api/screens")
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
            d = screen_to_dict(s)
            d["stale"] = s.build_id is not None and latest_bid is not None and s.build_id != latest_bid
            result.append(d)
        return result


@router.get("/api/screens/{screen_id}")
def get_screen(screen_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        s = db.query(ScreenLibrary).filter(ScreenLibrary.id == screen_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Screen not found")
        return screen_to_dict(s, include_xml=True)


@router.put("/api/screens/{screen_id}")
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
        return screen_to_dict(s)


@router.delete("/api/screens/{screen_id}")
def delete_screen(screen_id: int):
    with SessionLocal() as db:
        s = db.query(ScreenLibrary).filter(ScreenLibrary.id == screen_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Screen not found")
        db.delete(s)
        db.commit()
    return {"ok": True}


@router.get("/api/screens/{screen_id}/screenshot")
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
