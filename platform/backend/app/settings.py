from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


class Settings(BaseModel):
    data_dir: Path = ROOT_DIR / ".data"
    uploads_dir: Path = ROOT_DIR / "uploads"
    artifacts_dir: Path = ROOT_DIR / "artifacts"
    db_path: Path = ROOT_DIR / ".data" / "qa_platform.sqlite3"
    app_home_dir: Path = Path.home() / ".qa_platform"
    master_key_path: Path = Path.home() / ".qa_platform" / "master.key"

    # If blank, backend will assume Appium already running.
    appium_host: str = os.getenv("APPIUM_HOST", "127.0.0.1")
    appium_port: int = int(os.getenv("APPIUM_PORT", "4723"))


settings = Settings()


def ensure_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)


def _write_private_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def get_or_create_master_key() -> bytes:
    ensure_dirs()
    if settings.master_key_path.exists():
        key = settings.master_key_path.read_bytes().strip()
        if key:
            return key
    key = Fernet.generate_key()
    _write_private_bytes(settings.master_key_path, key + b"\n")
    return key


def load_encrypted_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}

    if payload.get("_encrypted") is not True:
        return payload

    token = payload.get("data")
    if not isinstance(token, str) or not token:
        return {}

    try:
        decrypted = Fernet(get_or_create_master_key()).decrypt(token.encode("utf-8"))
        data = json.loads(decrypted.decode("utf-8"))
    except (InvalidToken, ValueError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def save_encrypted_json(path: Path, data: dict[str, Any]) -> None:
    ensure_dirs()
    cipher = Fernet(get_or_create_master_key())
    encrypted = cipher.encrypt(json.dumps(data, indent=2).encode("utf-8")).decode("utf-8")
    payload = json.dumps({"_encrypted": True, "data": encrypted}, indent=2).encode("utf-8")
    _write_private_bytes(path, payload)
