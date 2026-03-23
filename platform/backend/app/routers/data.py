"""Data Layer — folders, data sets, CSV/JSON import/export."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse

from ..db import SessionLocal
from ..helpers import utcnow
from ..models import DataFolder, DataSet

router = APIRouter()

# ---------------------------------------------------------------------------
# Data Folders
# ---------------------------------------------------------------------------


@router.get("/api/data-folders")
def list_data_folders(project_id: int) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        folders = (
            db.query(DataFolder)
            .filter(DataFolder.project_id == project_id)
            .order_by(DataFolder.name)
            .all()
        )
        return [
            {
                "id": f.id,
                "project_id": f.project_id,
                "name": f.name,
                "description": f.description or "",
                "data_set_count": db.query(DataSet).filter(DataSet.folder_id == f.id).count(),
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in folders
        ]


@router.post("/api/data-folders")
def create_data_folder(body: dict[str, Any]) -> dict[str, Any]:
    project_id = body.get("project_id")
    name = (body.get("name") or "").strip()
    if not project_id or not name:
        raise HTTPException(status_code=400, detail="project_id and name are required")
    with SessionLocal() as db:
        existing = (
            db.query(DataFolder)
            .filter(DataFolder.project_id == project_id, DataFolder.name == name)
            .first()
        )
        if existing:
            raise HTTPException(status_code=409, detail=f"Folder '{name}' already exists")
        f = DataFolder(
            project_id=project_id,
            name=name,
            description=(body.get("description") or "").strip(),
        )
        db.add(f)
        db.commit()
        db.refresh(f)
        return {
            "id": f.id,
            "project_id": f.project_id,
            "name": f.name,
            "description": f.description or "",
            "data_set_count": 0,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }


@router.put("/api/data-folders/{folder_id}")
def update_data_folder(folder_id: int, body: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        f = db.query(DataFolder).filter(DataFolder.id == folder_id).first()
        if not f:
            raise HTTPException(status_code=404, detail="Folder not found")
        if "name" in body:
            f.name = (body["name"] or "").strip() or f.name
        if "description" in body:
            f.description = body["description"]
        db.commit()
        db.refresh(f)
        return {
            "id": f.id,
            "project_id": f.project_id,
            "name": f.name,
            "description": f.description or "",
            "data_set_count": db.query(DataSet).filter(DataSet.folder_id == f.id).count(),
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }


@router.delete("/api/data-folders/{folder_id}")
def delete_data_folder(folder_id: int):
    with SessionLocal() as db:
        f = db.query(DataFolder).filter(DataFolder.id == folder_id).first()
        if not f:
            raise HTTPException(status_code=404, detail="Folder not found")
        db.delete(f)
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Data Sets
# ---------------------------------------------------------------------------

def _ds_to_dict(ds: DataSet) -> dict[str, Any]:
    return {
        "id": ds.id,
        "project_id": ds.project_id,
        "folder_id": ds.folder_id,
        "name": ds.name,
        "description": ds.description or "",
        "environment": ds.environment or "",
        "variables": ds.variables or {},
        "rows": ds.rows or [],
        "is_default": bool(ds.is_default),
        "created_at": ds.created_at.isoformat() if ds.created_at else None,
        "updated_at": ds.updated_at.isoformat() if ds.updated_at else None,
    }


@router.get("/api/data-sets")
def list_data_sets(
    project_id: int,
    folder_id: Optional[int] = None,
    environment: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(DataSet).filter(DataSet.project_id == project_id)
        if folder_id is not None:
            q = q.filter(DataSet.folder_id == folder_id)
        if environment:
            q = q.filter(DataSet.environment == environment)
        datasets = q.order_by(DataSet.name).offset(offset).limit(limit).all()
        return [_ds_to_dict(ds) for ds in datasets]


@router.get("/api/data-sets/{ds_id}")
def get_data_set(ds_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        ds = db.query(DataSet).filter(DataSet.id == ds_id).first()
        if not ds:
            raise HTTPException(status_code=404, detail="Data set not found")
        return _ds_to_dict(ds)


@router.post("/api/data-sets")
def create_data_set(body: dict[str, Any]) -> dict[str, Any]:
    project_id = body.get("project_id")
    name = (body.get("name") or "").strip()
    if not project_id or not name:
        raise HTTPException(status_code=400, detail="project_id and name are required")
    with SessionLocal() as db:
        ds = DataSet(
            project_id=project_id,
            folder_id=body.get("folder_id"),
            name=name,
            description=(body.get("description") or "").strip(),
            environment=(body.get("environment") or "").strip(),
            variables=body.get("variables") or {},
            rows=body.get("rows") or [],
            is_default=1 if body.get("is_default") else 0,
        )
        db.add(ds)
        db.commit()
        db.refresh(ds)
        return _ds_to_dict(ds)


@router.put("/api/data-sets/{ds_id}")
def update_data_set(ds_id: int, body: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        ds = db.query(DataSet).filter(DataSet.id == ds_id).first()
        if not ds:
            raise HTTPException(status_code=404, detail="Data set not found")
        if "name" in body:
            ds.name = (body["name"] or "").strip() or ds.name
        if "description" in body:
            ds.description = body["description"]
        if "environment" in body:
            ds.environment = body["environment"]
        if "folder_id" in body:
            ds.folder_id = body["folder_id"]
        if "variables" in body:
            ds.variables = body["variables"]
        if "rows" in body:
            ds.rows = body["rows"]
        if "is_default" in body:
            if body["is_default"]:
                # Clear other defaults for this project
                db.query(DataSet).filter(
                    DataSet.project_id == ds.project_id,
                    DataSet.id != ds.id,
                    DataSet.is_default == 1,
                ).update({"is_default": 0})
            ds.is_default = 1 if body["is_default"] else 0
        ds.updated_at = utcnow()
        db.commit()
        db.refresh(ds)
        return _ds_to_dict(ds)


@router.delete("/api/data-sets/{ds_id}")
def delete_data_set(ds_id: int):
    with SessionLocal() as db:
        ds = db.query(DataSet).filter(DataSet.id == ds_id).first()
        if not ds:
            raise HTTPException(status_code=404, detail="Data set not found")
        db.delete(ds)
        db.commit()
    return {"ok": True}


@router.post("/api/data-sets/{ds_id}/duplicate")
def duplicate_data_set(ds_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        src = db.query(DataSet).filter(DataSet.id == ds_id).first()
        if not src:
            raise HTTPException(status_code=404, detail="Data set not found")
        copy = DataSet(
            project_id=src.project_id,
            folder_id=src.folder_id,
            name=f"{src.name} (copy)",
            description=src.description,
            environment=src.environment,
            variables=dict(src.variables or {}),
            rows=list(src.rows or []),
            is_default=0,
        )
        db.add(copy)
        db.commit()
        db.refresh(copy)
        return _ds_to_dict(copy)


@router.put("/api/data-sets/{ds_id}/default")
def set_default_data_set(ds_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        ds = db.query(DataSet).filter(DataSet.id == ds_id).first()
        if not ds:
            raise HTTPException(status_code=404, detail="Data set not found")
        db.query(DataSet).filter(
            DataSet.project_id == ds.project_id,
            DataSet.is_default == 1,
        ).update({"is_default": 0})
        ds.is_default = 1
        ds.updated_at = utcnow()
        db.commit()
        db.refresh(ds)
        return _ds_to_dict(ds)


# ---------------------------------------------------------------------------
# CSV / JSON Import / Export
# ---------------------------------------------------------------------------


@router.post("/api/data-sets/import/csv")
async def import_csv(
    project_id: int = Form(...),
    folder_id: Optional[int] = Form(None),
    name: str = Form(""),
    environment: str = Form(""),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    rows = [dict(row) for row in reader]
    if not rows:
        raise HTTPException(status_code=400, detail="CSV is empty or has no headers")
    ds_name = (name or file.filename or "Imported").strip().removesuffix(".csv")
    with SessionLocal() as db:
        ds = DataSet(
            project_id=project_id,
            folder_id=folder_id,
            name=ds_name,
            environment=environment.strip(),
            variables={},
            rows=rows,
        )
        db.add(ds)
        db.commit()
        db.refresh(ds)
        return _ds_to_dict(ds)


@router.get("/api/data-sets/{ds_id}/export/csv")
def export_csv(ds_id: int):
    with SessionLocal() as db:
        ds = db.query(DataSet).filter(DataSet.id == ds_id).first()
        if not ds:
            raise HTTPException(status_code=404, detail="Data set not found")

        buf = io.StringIO()
        rows = ds.rows or []
        variables = ds.variables or {}

        if rows:
            fieldnames = list(rows[0].keys()) if rows else []
            writer = csv.DictWriter(buf, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        elif variables:
            writer = csv.writer(buf)
            writer.writerow(["key", "value"])
            for k, v in variables.items():
                writer.writerow([k, str(v)])

        buf.seek(0)
        safe_name = ds.name.replace(" ", "_").replace("/", "_")
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.csv"'},
        )


@router.get("/api/data-sets/{ds_id}/export/json")
def export_json(ds_id: int):
    with SessionLocal() as db:
        ds = db.query(DataSet).filter(DataSet.id == ds_id).first()
        if not ds:
            raise HTTPException(status_code=404, detail="Data set not found")
        payload = {
            "name": ds.name,
            "environment": ds.environment or "",
            "variables": ds.variables or {},
            "rows": ds.rows or [],
        }
        content = json.dumps(payload, indent=2)
        safe_name = ds.name.replace(" ", "_").replace("/", "_")
        return StreamingResponse(
            iter([content]),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
        )
