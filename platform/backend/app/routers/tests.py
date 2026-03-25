"""Test CRUD, fix history, related tests."""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import SessionLocal
from ..helpers import steps_for_platform_record, test_out, utcnow
from ..models import Project, TestDefinition
from ..schemas import TestCreate, TestOut, TestUpdate

router = APIRouter()


@router.post("/api/projects/{project_id}/tests", response_model=TestOut)
def create_test(project_id: int, payload: TestCreate) -> TestOut:
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        ps: dict = dict(payload.platform_steps or {})
        legacy = list(payload.steps or [])
        if legacy and not ps.get("android"):
            ps["android"] = legacy
        if "android" not in ps:
            ps["android"] = []
        if "ios_sim" not in ps:
            ps["ios_sim"] = []
        android = list(ps.get("android") or [])
        t = TestDefinition(
            project_id=project_id,
            suite_id=payload.suite_id,
            prerequisite_test_id=payload.prerequisite_test_id,
            name=payload.name,
            steps=android,
            platform_steps=ps,
            acceptance_criteria=payload.acceptance_criteria,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return test_out(t)


@router.get("/api/projects/{project_id}/tests", response_model=list[TestOut])
def list_tests(project_id: int) -> list[TestOut]:
    with SessionLocal() as db:
        tests = db.query(TestDefinition).filter(TestDefinition.project_id == project_id).order_by(TestDefinition.created_at.desc()).all()
        return [test_out(t) for t in tests]


@router.put("/api/tests/{test_id}", response_model=TestOut)
def update_test(test_id: int, payload: TestUpdate, request: Request) -> TestOut:
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        provided = payload.model_fields_set
        if "name" in provided and payload.name is not None:
            t.name = payload.name
        if "suite_id" in provided:
            t.suite_id = payload.suite_id
        if "prerequisite_test_id" in provided:
            t.prerequisite_test_id = payload.prerequisite_test_id
        if "acceptance_criteria" in provided:
            t.acceptance_criteria = payload.acceptance_criteria

        if "platform_steps" in provided and payload.platform_steps is not None:
            current_ps = dict(t.platform_steps or {})
            for k, v in payload.platform_steps.items():
                if isinstance(v, list):
                    current_ps[str(k)] = v
            t.platform_steps = current_ps
            if "android" in current_ps:
                t.steps = list(current_ps["android"])
        elif "steps" in provided and payload.steps is not None:
            target_pf = payload.platform or "android"
            if target_pf not in ("android", "ios_sim"):
                target_pf = "android"
            current_ps = dict(t.platform_steps or {})
            current_ps[target_pf] = list(payload.steps)
            t.platform_steps = current_ps
            if target_pf == "android":
                t.steps = list(payload.steps)

        db.commit()
        db.refresh(t)
        return test_out(t)


class AppendFixHistoryRequest(BaseModel):
    analysis: str = ""
    fixed_steps: list[dict[str, Any]]
    changes: list[dict[str, Any]] = []
    run_id: Optional[int] = None
    steps_before_fix: Optional[list[dict[str, Any]]] = None
    target_platform: str = "android"


@router.post("/api/tests/{test_id}/append-fix-history")
def append_fix_history(test_id: int, payload: AppendFixHistoryRequest) -> dict[str, Any]:
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        history = list(getattr(t, "fix_history", None) or [])
        tp = payload.target_platform if payload.target_platform in ("android", "ios_sim") else "android"
        entry = {
            "analysis": payload.analysis,
            "fixed_steps": payload.fixed_steps,
            "changes": payload.changes,
            "run_id": payload.run_id,
            "target_platform": tp,
            "created_at": utcnow().isoformat(),
        }
        if payload.steps_before_fix is not None:
            entry["steps_before_fix"] = payload.steps_before_fix
        history.append(entry)
        t.fix_history = history[-10:]
        db.commit()
        return {"ok": True, "history_length": len(t.fix_history)}


@router.post("/api/tests/{test_id}/undo-last-fix")
def undo_last_fix(test_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        history = list(getattr(t, "fix_history", None) or [])
        if not history:
            raise HTTPException(status_code=400, detail="No fix history to undo")
        last = history[-1]
        target_pf = last.get("target_platform") or "android"
        if target_pf not in ("android", "ios_sim"):
            target_pf = "android"
        steps_before = last.get("steps_before_fix")
        if steps_before is None:
            raise HTTPException(status_code=400, detail="Cannot undo: previous steps not stored")
        ps = dict(t.platform_steps or {})
        ps[target_pf] = list(steps_before)
        t.platform_steps = ps
        if target_pf == "android":
            t.steps = list(steps_before)
        t.fix_history = history[:-1]
        db.commit()
        return {"ok": True, "steps": steps_before, "target_platform": target_pf}


@router.delete("/api/tests/{test_id}")
def delete_test(test_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        db.delete(t)
        db.commit()
        return {"ok": True}


def _steps_equal(a: dict, b: dict) -> bool:
    if a.get("type") != b.get("type"):
        return False
    if json.dumps(a.get("selector") or {}, sort_keys=True) != json.dumps(b.get("selector") or {}, sort_keys=True):
        return False
    if a.get("text") != b.get("text"):
        return False
    if a.get("expect") != b.get("expect"):
        return False
    return True


def _shared_prefix_length(steps_a: list[dict], steps_b: list[dict]) -> int:
    n = 0
    for i in range(min(len(steps_a), len(steps_b))):
        if _steps_equal(steps_a[i], steps_b[i]):
            n += 1
        else:
            break
    return n


@router.get("/api/tests/{test_id}/related")
def get_related_tests(test_id: int, failed_step_index: Optional[int] = None, platform: str = "android") -> dict[str, Any]:
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        project_id = t.project_id
        plat = platform if platform in ("android", "ios_sim") else "android"
        my_steps = steps_for_platform_record(t, plat)
        min_shared = (failed_step_index + 1) if failed_step_index is not None and failed_step_index >= 0 else 2

        dependents = db.query(TestDefinition).filter(
            TestDefinition.project_id == project_id,
            TestDefinition.prerequisite_test_id == test_id,
        ).all()

        similar: list[dict] = []
        others = db.query(TestDefinition).filter(
            TestDefinition.project_id == project_id,
            TestDefinition.id != test_id,
        ).all()
        dep_ids = {d.id for d in dependents}
        for o in others:
            if o.id in dep_ids:
                continue
            other_steps = steps_for_platform_record(o, plat)
            prefix_len = _shared_prefix_length(my_steps, other_steps)
            if prefix_len >= min_shared:
                has_failed_step = (
                    failed_step_index is not None
                    and failed_step_index < len(other_steps)
                    and failed_step_index < len(my_steps)
                    and _steps_equal(my_steps[failed_step_index], other_steps[failed_step_index])
                ) if failed_step_index is not None else True
                similar.append({
                    "test": test_out(o),
                    "shared_prefix_length": prefix_len,
                    "has_failed_step": has_failed_step,
                })

        return {
            "dependents": [test_out(d) for d in dependents],
            "similar": similar,
        }


class ApplyFixToRelatedRequest(BaseModel):
    fixed_steps: list[dict[str, Any]]
    prefix_length: int
    original_steps: list[dict[str, Any]]
    test_ids: list[int] = []
    target_platform: str = "android"


@router.post("/api/tests/{test_id}/apply-fix-to-related")
def apply_fix_to_related(test_id: int, payload: ApplyFixToRelatedRequest) -> dict[str, Any]:
    with SessionLocal() as db:
        t = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        project_id = t.project_id
        original = list(payload.original_steps or [])
        plat = payload.target_platform if payload.target_platform in ("android", "ios_sim") else "android"

        related = db.query(TestDefinition).filter(
            TestDefinition.project_id == project_id,
            TestDefinition.id != test_id,
            TestDefinition.prerequisite_test_id != test_id,
        ).all()

        updated: list[int] = []
        prefix_len = min(payload.prefix_length, len(original), len(payload.fixed_steps))
        target_ids = set(payload.test_ids) if payload.test_ids else None

        for o in related:
            if target_ids is not None and o.id not in target_ids:
                continue
            other_steps = steps_for_platform_record(o, plat)
            shared = _shared_prefix_length(original, other_steps)
            if shared < prefix_len:
                continue
            new_steps = list(payload.fixed_steps[:prefix_len]) + other_steps[prefix_len:]
            ps = dict(o.platform_steps or {})
            ps[plat] = new_steps
            o.platform_steps = ps
            if plat == "android":
                o.steps = new_steps
            updated.append(o.id)

        db.commit()
        return {"updated_test_ids": updated}
