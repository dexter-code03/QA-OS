"""Health, auth token, settings, onboarding."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..helpers import (
    ALLOWED_ORIGINS,
    AUTH_COOKIE_NAME,
    ONBOARDING_FILE,
    get_auth_token,
    load_settings,
    save_settings_file,
)
from ..settings import ensure_dirs

router = APIRouter()


@router.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@router.get("/api/auth/token")
def issue_auth_token(request: Request) -> JSONResponse:
    if request.headers.get("origin") not in (None, "", *ALLOWED_ORIGINS):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Origin not allowed")
    token = get_auth_token()
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
    data = {"completed": True, "completed_at": datetime.utcnow().isoformat()}
    ONBOARDING_FILE.write_text(json.dumps(data))
    return data
