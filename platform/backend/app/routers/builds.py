"""Build upload, list, delete."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..db import SessionLocal
from ..models import Build, Project
from ..schemas import BuildOut
from ..settings import ensure_dirs, settings

router = APIRouter()

_BUILD_ALLOWED_EXTENSIONS = {".apk", ".ipa", ".app", ".zip"}
_BUILD_MAX_SIZE_BYTES = 500 * 1024 * 1024


def _find_aapt() -> str:
    candidates = [os.path.expanduser("~/Library/Android/sdk/build-tools")]
    for base in candidates:
        if os.path.exists(base):
            versions = sorted(os.listdir(base), reverse=True)
            for v in versions:
                aapt = os.path.join(base, v, "aapt")
                if os.path.exists(aapt):
                    return aapt
    return "aapt"


def _parse_apk_manifest(apk_path: str) -> dict:
    try:
        aapt = _find_aapt()
        # argv list (no shell); path is server-side upload under sanitized safe_fname
        apk_arg = str(Path(apk_path).resolve())
        result = subprocess.run([aapt, "dump", "badging", apk_arg], capture_output=True, text=True, timeout=10)
        output = result.stdout
    except Exception:
        return {"file_name": Path(apk_path).name, "file_size_mb": round(os.path.getsize(apk_path) / 1024 / 1024, 1)}

    def extract(pattern, default=""):
        m = re.search(pattern, output)
        return m.group(1) if m else default

    return {
        "display_name":   extract(r"application-label:'([^']+)'"),
        "version_name":   extract(r"versionName='([^']+)'"),
        "version_code":   extract(r"versionCode='([^']+)'"),
        "package":        extract(r"package: name='([^']+)'"),
        "main_activity":  extract(r"launchable-activity: name='([^']+)'"),
        "min_sdk":        extract(r"sdkVersion:'([^']+)'"),
        "target_sdk":     extract(r"targetSdkVersion:'([^']+)'"),
        "file_name":      Path(apk_path).name,
        "file_size_mb":   round(os.path.getsize(apk_path) / 1024 / 1024, 1),
    }


def _sanitize_build_filename(name: str) -> str:
    p = Path(name or "build")
    safe = p.name
    safe = re.sub(r"[^\w\-\.]", "_", safe)
    return safe or "build"


def _build_out(b: Build) -> BuildOut:
    return BuildOut(
        id=b.id,
        project_id=b.project_id,
        platform=b.platform,
        file_name=b.file_name,
        created_at=b.created_at,
        metadata=b.build_metadata or {},
    )


@router.post("/api/projects/{project_id}/builds", response_model=BuildOut)
async def upload_build(project_id: int, platform: str, file: UploadFile = File(...)) -> BuildOut:
    if platform not in ("android", "ios_sim"):
        raise HTTPException(status_code=400, detail="platform must be android|ios_sim")

    fname_lower = (file.filename or "").lower()
    ext_ok = any(fname_lower.endswith(ext) for ext in _BUILD_ALLOWED_EXTENSIONS) or fname_lower.endswith(".app.zip")
    if not ext_ok:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(sorted(_BUILD_ALLOWED_EXTENSIONS))}, .app.zip",
        )

    if fname_lower.endswith((".app", ".app.zip", ".ipa")):
        platform = "ios_sim"
    elif fname_lower.endswith(".apk"):
        platform = "android"

    safe_fname = _sanitize_build_filename(file.filename)
    ensure_dirs()
    out_dir = settings.uploads_dir / str(project_id) / platform
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / safe_fname

    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(out_dir))
    try:
        size = 0
        with os.fdopen(tmp_fd, "wb") as tmp:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _BUILD_MAX_SIZE_BYTES:
                    os.unlink(tmp_path)
                    raise HTTPException(status_code=413, detail="File exceeds 500MB limit")
                tmp.write(chunk)
        shutil.move(tmp_path, str(dest))
    except HTTPException:
        raise
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    meta: dict = {}
    if platform == "android" and str(dest).endswith(".apk"):
        meta = _parse_apk_manifest(str(dest))
    elif platform == "ios_sim":
        meta["bundle_id"] = ""
        meta["display_name"] = Path(safe_fname).stem

    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        b = Build(
            project_id=project_id,
            platform=platform,
            file_name=safe_fname,
            file_path=str(dest),
            build_metadata=meta,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return _build_out(b)


@router.get("/api/projects/{project_id}/builds", response_model=list[BuildOut])
def list_builds(project_id: int, limit: int = 100, offset: int = 0) -> list[BuildOut]:
    with SessionLocal() as db:
        builds = (
            db.query(Build)
            .filter(Build.project_id == project_id)
            .order_by(Build.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        return [_build_out(b) for b in builds]


@router.delete("/api/builds/{build_id}")
def delete_build(build_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        b = db.query(Build).filter(Build.id == build_id).first()
        if not b:
            raise HTTPException(status_code=404, detail="Build not found")
        file_path = Path(b.file_path) if b.file_path else None
        db.delete(b)
        db.commit()
    if file_path and file_path.exists():
        try:
            file_path.unlink()
        except OSError:
            pass
    return {"ok": True}
