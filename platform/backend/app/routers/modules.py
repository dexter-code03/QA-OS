"""Module and Suite CRUD."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import SessionLocal
from ..models import Module, Project, TestDefinition, TestSuite
from ..schemas import ModuleCreate, ModuleOut, SuiteCreate, SuiteOut

router = APIRouter()


@router.post("/api/projects/{project_id}/modules", response_model=ModuleOut)
def create_module(project_id: int, payload: ModuleCreate) -> ModuleOut:
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        m = Module(project_id=project_id, name=payload.name)
        db.add(m)
        db.commit()
        db.refresh(m)
        return ModuleOut(id=m.id, project_id=m.project_id, name=m.name, created_at=m.created_at)


@router.get("/api/projects/{project_id}/modules", response_model=list[ModuleOut])
def list_modules(project_id: int) -> list[ModuleOut]:
    with SessionLocal() as db:
        mods = db.query(Module).filter(Module.project_id == project_id).order_by(Module.created_at.desc()).all()
        return [ModuleOut(id=m.id, project_id=m.project_id, name=m.name, created_at=m.created_at) for m in mods]


@router.put("/api/modules/{module_id}", response_model=ModuleOut)
def update_module(module_id: int, payload: ModuleCreate) -> ModuleOut:
    with SessionLocal() as db:
        m = db.query(Module).filter(Module.id == module_id).first()
        if not m:
            raise HTTPException(status_code=404, detail="Module not found")
        m.name = payload.name
        db.commit()
        db.refresh(m)
        return ModuleOut(id=m.id, project_id=m.project_id, name=m.name, created_at=m.created_at)


@router.delete("/api/modules/{module_id}")
def delete_module(module_id: int):
    with SessionLocal() as db:
        m = db.query(Module).filter(Module.id == module_id).first()
        if not m:
            raise HTTPException(status_code=404, detail="Module not found")
        sids = [s.id for s in db.query(TestSuite).filter(TestSuite.module_id == module_id).all()]
        if sids:
            db.query(TestDefinition).filter(TestDefinition.suite_id.in_(sids)).update(
                {TestDefinition.suite_id: None}, synchronize_session=False
            )
        db.delete(m)
        db.commit()
    return {"ok": True}


@router.post("/api/modules/{module_id}/suites", response_model=SuiteOut)
def create_suite(module_id: int, payload: SuiteCreate) -> SuiteOut:
    with SessionLocal() as db:
        m = db.query(Module).filter(Module.id == module_id).first()
        if not m:
            raise HTTPException(status_code=404, detail="Module not found")
        s = TestSuite(module_id=module_id, name=payload.name)
        db.add(s)
        db.commit()
        db.refresh(s)
        return SuiteOut(id=s.id, module_id=s.module_id, name=s.name, created_at=s.created_at)


@router.get("/api/modules/{module_id}/suites", response_model=list[SuiteOut])
def list_suites(module_id: int) -> list[SuiteOut]:
    with SessionLocal() as db:
        suites = db.query(TestSuite).filter(TestSuite.module_id == module_id).order_by(TestSuite.created_at.desc()).all()
        return [SuiteOut(id=s.id, module_id=s.module_id, name=s.name, created_at=s.created_at) for s in suites]


@router.put("/api/suites/{suite_id}", response_model=SuiteOut)
def update_suite(suite_id: int, payload: SuiteCreate) -> SuiteOut:
    with SessionLocal() as db:
        s = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Suite not found")
        s.name = payload.name
        db.commit()
        db.refresh(s)
        return SuiteOut(id=s.id, module_id=s.module_id, name=s.name, created_at=s.created_at)


@router.delete("/api/suites/{suite_id}")
def delete_suite(suite_id: int):
    with SessionLocal() as db:
        s = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Suite not found")
        db.query(TestDefinition).filter(TestDefinition.suite_id == suite_id).update(
            {TestDefinition.suite_id: None}, synchronize_session=False
        )
        db.delete(s)
        db.commit()
    return {"ok": True}
