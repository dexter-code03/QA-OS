from __future__ import annotations

from pathlib import Path
import os

from dotenv import load_dotenv
from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


class Settings(BaseModel):
    data_dir: Path = ROOT_DIR / ".data"
    uploads_dir: Path = ROOT_DIR / "uploads"
    artifacts_dir: Path = ROOT_DIR / "artifacts"
    db_path: Path = ROOT_DIR / ".data" / "qa_platform.sqlite3"

    # If blank, backend will assume Appium already running.
    appium_host: str = os.getenv("APPIUM_HOST", "127.0.0.1")
    appium_port: int = int(os.getenv("APPIUM_PORT", "4723"))


settings = Settings()


def ensure_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)

