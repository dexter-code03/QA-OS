"""Project CRUD."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import SessionLocal
from ..models import Project
from ..schemas import ProjectCreate, ProjectOut

router = APIRouter()


@router.post("/api/projects", response_model=ProjectOut)
def create_project(payload: ProjectCreate) -> ProjectOut:
    with SessionLocal() as db:
        existing = db.query(Project).filter(Project.name == payload.name).first()
        if existing:
            raise HTTPException(status_code=409, detail="Project name already exists")
        p = Project(name=payload.name)
        db.add(p)
        db.commit()
        db.refresh(p)
        return ProjectOut(id=p.id, name=p.name, created_at=p.created_at)


@router.get("/api/projects", response_model=list[ProjectOut])
def list_projects() -> list[ProjectOut]:
    with SessionLocal() as db:
        projects = db.query(Project).order_by(Project.created_at.desc()).all()
        return [ProjectOut(id=p.id, name=p.name, created_at=p.created_at) for p in projects]


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: int) -> dict[str, bool]:
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        db.delete(p)
        db.commit()
        return {"ok": True}
