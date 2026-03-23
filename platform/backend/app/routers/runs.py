"""Run CRUD, cancel, delete, triage."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from ..db import SessionLocal
from ..events import RunEvent, event_bus
from ..helpers import classify_failure_message, run_to_out, utcnow
from ..models import BatchRun, Build, DataSet, Project, Run, TestDefinition
from ..runner.engine import RunEngine, run_engine
from ..schemas import RunCreate, RunOut

router = APIRouter()


@router.post("/api/runs")
async def create_run(payload: RunCreate):
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

        # Resolve data set: explicit id or by environment
        data_set_id = payload.data_set_id
        if not data_set_id and payload.environment:
            ds = db.query(DataSet).filter(
                DataSet.project_id == payload.project_id,
                DataSet.environment == payload.environment,
            ).first()
            if ds:
                data_set_id = ds.id

        # Check for data-driven: if data set has rows, expand into batch
        ds_obj = db.query(DataSet).filter(DataSet.id == data_set_id).first() if data_set_id else None
        rows = (ds_obj.rows or []) if ds_obj else []

        if len(rows) > 1:
            # Data-driven: create a batch run with one child per row
            batch = BatchRun(
                project_id=payload.project_id,
                mode="data-driven",
                source_id=payload.test_id,
                source_name=t.name,
                platform=payload.platform,
                status="queued",
                total=len(rows),
                started_at=utcnow(),
            )
            db.add(batch)
            db.commit()
            db.refresh(batch)

            first_run = None
            for idx in range(len(rows)):
                r = Run(
                    project_id=payload.project_id,
                    build_id=payload.build_id,
                    test_id=payload.test_id,
                    batch_run_id=batch.id,
                    status="queued",
                    device_target=payload.device_target,
                    platform=payload.platform,
                    data_set_id=data_set_id,
                    data_row_index=idx,
                    artifacts={},
                    summary={},
                )
                db.add(r)
                db.commit()
                db.refresh(r)
                if first_run is None:
                    first_run = r
                asyncio.create_task(event_bus.publish(RunEvent(run_id=r.id, type="queued", payload={"runId": r.id})))
                asyncio.create_task(run_engine.enqueue(r.id))

            return run_to_out(first_run)
        else:
            # Single run (optionally with data set for key-value variables)
            r = Run(
                project_id=payload.project_id,
                build_id=payload.build_id,
                test_id=payload.test_id,
                status="queued",
                device_target=payload.device_target,
                platform=payload.platform,
                data_set_id=data_set_id,
                data_row_index=0 if rows else None,
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
def list_runs(project_id: int, limit: int = 100, offset: int = 0) -> list[RunOut]:
    with SessionLocal() as db:
        runs = (
            db.query(Run)
            .filter(Run.project_id == project_id)
            .order_by(Run.id.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
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
        r.finished_at = r.finished_at or utcnow()
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
