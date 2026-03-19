from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.options.ios import XCUITestOptions

from ..settings import settings


def _latest_ios_runtime_version() -> str:
    """Return the latest available iOS simulator SDK version (e.g. '26.3')."""
    try:
        out = subprocess.check_output(
            ["xcrun", "simctl", "list", "runtimes", "-j"], text=True, timeout=10
        )
        data = json.loads(out)
        versions = []
        for r in data.get("runtimes", []):
            if "iOS" in (r.get("name") or "") and r.get("isAvailable"):
                v = r.get("version", "")
                if v:
                    versions.append(v)
        return max(versions, key=lambda x: [int(p) for p in x.split(".")]) if versions else ""
    except Exception:
        return ""


def _ios_udid_exists(udid: str) -> bool:
    """Check if the given UDID exists in the current simctl device list."""
    if not udid or "-" not in udid:
        return False
    try:
        out = subprocess.check_output(
            ["xcrun", "simctl", "list", "devices", "-j"], text=True, timeout=10
        )
        data = json.loads(out)
        for devs in data.get("devices", {}).values():
            for d in devs:
                if d.get("udid") == udid:
                    return True
        return False
    except Exception:
        return False


@dataclass(frozen=True)
class SessionConfig:
    platform: str  # android | ios_sim
    device_target: str = ""
    app_path: Optional[str] = None
    build_meta: dict = field(default_factory=dict)


def create_driver(cfg: SessionConfig) -> webdriver.Remote:
    server_url = f"http://{settings.appium_host}:{settings.appium_port}"
    meta = cfg.build_meta or {}

    if cfg.platform == "android":
        opts = UiAutomator2Options()
        opts.platform_name = "Android"
        opts.automation_name = "UiAutomator2"
        if cfg.device_target:
            opts.udid = cfg.device_target
        if cfg.app_path:
            opts.app = cfg.app_path
        opts.auto_grant_permissions = True
        opts.new_command_timeout = 180
        opts.no_reset = False
        pkg = meta.get("package")
        act = meta.get("main_activity")
        if pkg:
            opts.app_package = pkg
        if act:
            opts.app_activity = act
        return webdriver.Remote(command_executor=server_url, options=opts)

    if cfg.platform == "ios_sim":
        opts = XCUITestOptions()
        opts.platform_name = "iOS"
        opts.automation_name = "XCUITest"
        # Use UDID only if it exists (avoids "26.2 does not exist" when Xcode updated and old simulators removed)
        # Otherwise set platformVersion to latest available SDK
        device_target = cfg.device_target if _ios_udid_exists(cfg.device_target) else ""
        if device_target:
            opts.udid = device_target
        else:
            platform_ver = _latest_ios_runtime_version()
            if platform_ver:
                opts.platform_version = platform_ver
        if cfg.app_path:
            opts.app = cfg.app_path
        opts.auto_accept_alerts = True
        opts.new_command_timeout = 180
        opts.no_reset = False
        bundle_id = meta.get("bundle_id")
        if bundle_id:
            opts.bundle_id = bundle_id
        return webdriver.Remote(command_executor=server_url, options=opts)

    raise ValueError(f"Unsupported platform: {cfg.platform}")
