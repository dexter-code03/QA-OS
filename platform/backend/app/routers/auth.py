"""Health, auth token, settings, onboarding."""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..helpers import (
    ALLOWED_ORIGINS,
    AUTH_COOKIE_NAME,
    AUTH_TOKEN_FILE,
    ONBOARDING_FILE,
    get_auth_token,
    load_settings,
    save_settings_file,
    utcnow,
)
from ..settings import ensure_dirs, settings

router = APIRouter()

_BOOT_TIME = time.monotonic()
_VERSION = "0.1.0"


@router.get("/api/health")
def health() -> dict[str, Any]:
    uptime_s = int(time.monotonic() - _BOOT_TIME)
    db_ok = settings.db_path.exists()
    return {
        "status": "ok",
        "version": _VERSION,
        "uptime_seconds": uptime_s,
        "database": "connected" if db_ok else "missing",
        "time": utcnow().isoformat(),
    }


def _set_auth_cookie(response: JSONResponse, token: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


@router.get("/api/auth/token")
def issue_auth_token(request: Request) -> JSONResponse:
    if request.headers.get("origin") not in (None, "", *ALLOWED_ORIGINS):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Origin not allowed")
    token = get_auth_token()
    response = JSONResponse({"token": token})
    _set_auth_cookie(response, token)
    return response


@router.post("/api/auth/rotate")
def rotate_auth_token(request: Request) -> JSONResponse:
    """Generate a new auth token, invalidating the old one."""
    if request.headers.get("origin") not in (None, "", *ALLOWED_ORIGINS):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Origin not allowed")
    import secrets
    new_token = secrets.token_urlsafe(32)
    ensure_dirs()
    AUTH_TOKEN_FILE.write_text(f"{new_token}\n", encoding="utf-8")
    response = JSONResponse({"token": new_token, "rotated": True})
    _set_auth_cookie(response, new_token)
    return response


@router.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return load_settings()


@router.post("/api/settings")
def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    current.update(payload)
    save_settings_file(current)
    return current


@router.get("/api/onboarding")
def onboarding_status() -> dict[str, Any]:
    if ONBOARDING_FILE.exists():
        return json.loads(ONBOARDING_FILE.read_text())
    return {"completed": False}


@router.post("/api/onboarding/complete")
def onboarding_complete() -> dict[str, Any]:
    ensure_dirs()
    data = {"completed": True, "completed_at": utcnow().isoformat()}
    ONBOARDING_FILE.write_text(json.dumps(data))
    return data


@router.post("/api/admin/cleanup")
def cleanup_old_data(days: int = 90) -> dict[str, Any]:
    """Delete runs older than `days` and clean up orphan artifact files."""
    from datetime import timedelta
    from pathlib import Path

    from ..db import SessionLocal
    from ..models import Run

    cutoff = utcnow() - timedelta(days=days)
    deleted_runs = 0
    cleaned_files = 0

    with SessionLocal() as db:
        old_runs = db.query(Run).filter(Run.finished_at < cutoff, Run.finished_at.isnot(None)).all()
        for r in old_runs:
            artifacts_dir = settings.artifacts_dir / str(r.project_id) / str(r.id)
            if artifacts_dir.exists():
                import shutil
                try:
                    shutil.rmtree(str(artifacts_dir))
                    cleaned_files += 1
                except OSError:
                    pass
            db.delete(r)
            deleted_runs += 1
        db.commit()

    return {"deleted_runs": deleted_runs, "cleaned_artifact_dirs": cleaned_files, "cutoff_days": days}
