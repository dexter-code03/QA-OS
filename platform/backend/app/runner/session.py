from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.options.ios import XCUITestOptions

from ..settings import settings


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
        if cfg.device_target:
            opts.udid = cfg.device_target
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
