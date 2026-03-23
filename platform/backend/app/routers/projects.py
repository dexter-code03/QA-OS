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
