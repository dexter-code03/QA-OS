"""Run CRUD, cancel, delete, triage."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from ..db import SessionLocal
from ..events import RunEvent, event_bus
from ..helpers import classify_failure_message, run_to_out
from ..models import Build, Project, Run, TestDefinition
from ..runner.engine import RunEngine, run_engine
from ..schemas import RunCreate, RunOut

router = APIRouter()


@router.post("/api/runs", response_model=RunOut)
async def create_run(payload: RunCreate) -> RunOut:
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == payload.project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        t = db.query(TestDefinition).filter(TestDefinition.id == payload.test_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")

        b = None
        if payload.build_id is not None:
            b = db.query(Build).filter(Build.id == payload.build_id).first()
            if not b:
                raise HTTPException(status_code=404, detail="Build not found")

        r = Run(
            project_id=payload.project_id,
            build_id=payload.build_id,
            test_id=payload.test_id,
            status="queued",
            device_target=payload.device_target,
            platform=payload.platform,
            artifacts={},
            summary={},
        )
        db.add(r)
        db.commit()
        db.refresh(r)

        asyncio.create_task(event_bus.publish(RunEvent(run_id=r.id, type="queued", payload={"runId": r.id})))
        asyncio.create_task(run_engine.enqueue(r.id))

        return run_to_out(r)


@router.get("/api/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int) -> RunOut:
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        return run_to_out(r)


@router.get("/api/projects/{project_id}/runs", response_model=list[RunOut])
def list_runs(project_id: int) -> list[RunOut]:
    with SessionLocal() as db:
        runs = db.query(Run).filter(Run.project_id == project_id).order_by(Run.id.desc()).limit(100).all()
        return [run_to_out(r) for r in runs]


@router.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        if r.status not in ("queued", "running"):
            return {"ok": True, "status": r.status, "message": f"Run already {r.status}"}
        run_engine.request_cancel(run_id)
        r.status = "cancelled"
        r.finished_at = r.finished_at or datetime.utcnow()
        db.commit()
    RunEngine._sync_batch_counters(run_id)
    return {"ok": True, "message": "Run cancelled"}


@router.delete("/api/runs/{run_id}")
def delete_run(run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        db.delete(r)
        db.commit()
        return {"ok": True}


@router.post("/api/runs/{run_id}/triage")
def triage_run(run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        if r.status not in ("failed", "error"):
            return {"classifications": [], "note": "Run is not failed/error"}
        stack = r.error_message or ""
        summary = (r.summary or {}) if isinstance(r.summary, dict) else {}
        step_results = summary.get("stepResults") or []
        failed_step = next((x for x in step_results if isinstance(x, dict) and x.get("status") == "failed"), None)
        if failed_step and isinstance(failed_step.get("details"), dict):
            err = failed_step["details"].get("error")
            if err:
                stack = str(err)
        c = classify_failure_message(stack, r.platform)
        return {
            "classifications": [
                {
                    "testCaseId": f"RUN-{r.id}",
                    "category": c["category"],
                    "summary": c["summary"],
                    "platform": c["platform"],
                }
            ]
        }
