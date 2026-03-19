from __future__ import annotations

from pathlib import Path
from typing import Optional

from appium.webdriver.webdriver import WebDriver


def ensure_run_dir(artifacts_root: Path, project_id: int, run_id: int) -> Path:
    d = artifacts_root / str(project_id) / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_screenshot(driver: WebDriver, run_dir: Path, name: str) -> str:
    path = run_dir / name
    driver.get_screenshot_as_file(str(path))
    return str(path.name)


def save_page_source(driver: WebDriver, run_dir: Path, name: str) -> str:
    path = run_dir / name
    path.write_text(driver.page_source or "", encoding="utf-8")
    return str(path.name)

