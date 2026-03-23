"""Batch run CRUD and cancel."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from ..db import SessionLocal
from ..events import RunEvent, event_bus
from ..helpers import batch_to_out
from ..models import BatchRun, Module, Project, Run, TestDefinition, TestSuite
from ..runner.engine import run_engine
from ..schemas import BatchRunCreate, BatchRunOut

router = APIRouter()


@router.post("/api/batch-runs", response_model=BatchRunOut)
async def create_batch_run(payload: BatchRunCreate) -> BatchRunOut:
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == payload.project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")

        if payload.mode == "suite":
            suite = db.query(TestSuite).filter(TestSuite.id == payload.source_id).first()
            if not suite:
                raise HTTPException(status_code=404, detail="Suite not found")
            source_name = suite.name
            test_ids = [t.id for t in db.query(TestDefinition).filter(TestDefinition.suite_id == payload.source_id).all()]
        elif payload.mode == "collection":
            module = db.query(Module).filter(Module.id == payload.source_id).first()
            if not module:
                raise HTTPException(status_code=404, detail="Collection not found")
            source_name = module.name
            suite_ids = [s.id for s in db.query(TestSuite).filter(TestSuite.module_id == payload.source_id).all()]
            test_ids = [t.id for t in db.query(TestDefinition).filter(TestDefinition.suite_id.in_(suite_ids)).all()] if suite_ids else []
        else:
            raise HTTPException(status_code=400, detail=f"Invalid mode: {payload.mode}")

        if not test_ids:
            raise HTTPException(status_code=400, detail="No tests found for the selected scope")

        batch = BatchRun(
            project_id=payload.project_id,
            mode=payload.mode,
            source_id=payload.source_id,
            source_name=source_name,
            platform=payload.platform,
            status="queued",
            total=len(test_ids),
            started_at=datetime.utcnow(),
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)

        child_runs: list[Run] = []
        for tid in test_ids:
            r = Run(
                project_id=payload.project_id,
                build_id=payload.build_id,
                test_id=tid,
                batch_run_id=batch.id,
                status="queued",
                device_target=payload.device_target,
                platform=payload.platform,
                artifacts={},
                summary={},
            )
            db.add(r)
            child_runs.append(r)
        db.commit()
        for r in child_runs:
            db.refresh(r)

        batch.status = "running"
        db.commit()

        for r in child_runs:
            await event_bus.publish(RunEvent(run_id=r.id, type="queued", payload={"runId": r.id, "batchRunId": batch.id}))
            await run_engine.enqueue(r.id)

        return batch_to_out(batch, db)


@router.get("/api/batch-runs/{batch_id}", response_model=BatchRunOut)
def get_batch_run(batch_id: int) -> BatchRunOut:
    with SessionLocal() as db:
        batch = db.query(BatchRun).filter(BatchRun.id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch run not found")
        return batch_to_out(batch, db)


@router.get("/api/projects/{project_id}/batch-runs")
def list_batch_runs(project_id: int) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        batches = db.query(BatchRun).filter(BatchRun.project_id == project_id).order_by(BatchRun.id.desc()).limit(20).all()
        return [batch_to_out(b, db).dict() for b in batches]


@router.post("/api/batch-runs/{batch_id}/cancel")
def cancel_batch_run(batch_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        batch = db.query(BatchRun).filter(BatchRun.id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch run not found")
        children = db.query(Run).filter(Run.batch_run_id == batch_id, Run.status.in_(("queued", "running"))).all()
        cancelled_count = 0
        for r in children:
            if r.status == "running":
                run_engine.request_cancel(r.id)
            r.status = "cancelled"
            r.finished_at = r.finished_at or datetime.utcnow()
            cancelled_count += 1
        db.commit()

        all_children = db.query(Run).filter(Run.batch_run_id == batch_id).all()
        passed = sum(1 for c in all_children if c.status == "passed")
        failed = sum(1 for c in all_children if c.status in ("failed", "error"))
        cancelled_n = sum(1 for c in all_children if c.status == "cancelled")
        batch.passed = passed
        batch.failed = failed
        batch.finished_at = batch.finished_at or datetime.utcnow()
        if cancelled_n == batch.total:
            batch.status = "cancelled"
        elif failed == 0 and passed == batch.total:
            batch.status = "passed"
        else:
            batch.status = "partial"
        db.commit()
    return {"ok": True, "message": f"Cancelled {cancelled_count} runs"}
