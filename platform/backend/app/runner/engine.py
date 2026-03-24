from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from appium.webdriver.webdriver import WebDriver

from ..db import SessionLocal, Run, BatchRun, TestDefinition, Build, _classify_error
from ..events import RunEvent, event_bus
from ..settings import settings
from .appium_service import ensure_appium_running
from .artifacts import ensure_run_dir, save_page_source, save_screenshot
from .debug_listener import wrap_driver_with_debug
from .executor import run_steps
from .recording_android import start_screenrecord, stop_and_pull
from .recording_ios_sim import start_recording as start_ios_recording, stop as stop_ios_recording
from .video_compat import postprocess_mp4_for_broad_playback, transcode_mov_to_mp4
from .session import SessionConfig, create_driver
from .steps import parse_steps


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class EnqueuedRun:
    run_id: int


_APPIUM_IDLE_TIMEOUT_S = 300  # kill managed Appium after 5 min idle


class RunEngine:
    def __init__(self, max_workers: int = 1) -> None:
        self._q: asyncio.Queue[EnqueuedRun] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._max_workers = max_workers
        self._pending_cancel: set[int] = set()
        self._cancel_events: dict[int, threading.Event] = {}
        self._last_run_finished: float = 0.0
        self._managed_appium: Optional[object] = None  # AppiumHandle or None

    def request_cancel(self, run_id: int) -> None:
        ev = self._cancel_events.get(run_id)
        if ev is not None:
            ev.set()
        else:
            self._pending_cancel.add(run_id)

    def is_cancelled(self, run_id: int) -> bool:
        ev = self._cancel_events.get(run_id)
        if ev is not None:
            return ev.is_set()
        return run_id in self._pending_cancel

    def is_actively_executing(self, run_id: int) -> bool:
        """True if _execute is currently processing this run_id."""
        return run_id in self._cancel_events

    def clear_cancel(self, run_id: int) -> None:
        self._pending_cancel.discard(run_id)
        self._cancel_events.pop(run_id, None)

    def start(self) -> None:
        if self._tasks:
            return
        for _ in range(self._max_workers):
            self._tasks.append(asyncio.create_task(self._loop()))

    async def enqueue(self, run_id: int) -> None:
        await self._q.put(EnqueuedRun(run_id=run_id))

    async def _loop(self) -> None:
        import time as _time
        while True:
            try:
                item = await asyncio.wait_for(self._q.get(), timeout=2.0)
            except asyncio.TimeoutError:
                self._maybe_kill_idle_appium(_time.monotonic())
                continue
            try:
                await self._execute(item.run_id)
            except Exception as e:
                await event_bus.publish(RunEvent(run_id=item.run_id, type="engine_error", payload={"error": str(e)}))
            self._last_run_finished = _time.monotonic()

    def _maybe_kill_idle_appium(self, now: float) -> None:
        handle = self._managed_appium
        if handle is None or not self._last_run_finished:
            return
        if now - self._last_run_finished > _APPIUM_IDLE_TIMEOUT_S:
            try:
                if hasattr(handle, "process") and handle.process and handle.process.poll() is None:
                    handle.process.terminate()
            except Exception:
                pass
            self._managed_appium = None

    @staticmethod
    def _sync_batch_counters(run_id: int) -> None:
        """Recalculate batch counters atomically using SQL subqueries."""
        from sqlalchemy import text
        with SessionLocal() as db:
            r = db.query(Run).filter(Run.id == run_id).first()
            if not r or not getattr(r, "batch_run_id", None):
                return
            bid = r.batch_run_id
            db.execute(text("""
                UPDATE batch_runs SET
                    passed = (SELECT count(*) FROM runs WHERE batch_run_id = :bid AND status = 'passed'),
                    failed = (SELECT count(*) FROM runs WHERE batch_run_id = :bid AND status IN ('failed','error'))
                WHERE id = :bid
            """), {"bid": bid})
            db.commit()
            batch = db.query(BatchRun).filter(BatchRun.id == bid).first()
            if not batch:
                return
            children = db.query(Run).filter(Run.batch_run_id == bid).all()
            passed = batch.passed
            failed = batch.failed
            cancelled = sum(1 for c in children if c.status == "cancelled")
            done = passed + failed + cancelled
            if done >= batch.total:
                batch.finished_at = _utcnow()
                if cancelled == batch.total:
                    batch.status = "cancelled"
                elif failed == 0 and passed == batch.total:
                    batch.status = "passed"
                elif passed == 0 and failed == batch.total:
                    batch.status = "failed"
                else:
                    batch.status = "partial"
            elif any(c.status == "running" for c in children):
                batch.status = "running"
            db.commit()

    async def _execute(self, run_id: int) -> None:
        cancel_ev = threading.Event()
        if run_id in self._pending_cancel:
            cancel_ev.set()
            self._pending_cancel.discard(run_id)
        self._cancel_events[run_id] = cancel_ev

        if cancel_ev.is_set():
            with SessionLocal() as db:
                r = db.query(Run).filter(Run.id == run_id).first()
                if r:
                    r.status = "cancelled"
                    r.finished_at = _utcnow()
                    db.commit()
            self.clear_cancel(run_id)
            await event_bus.publish(RunEvent(run_id=run_id, type="finished", payload={"status": "cancelled"}))
            self._sync_batch_counters(run_id)
            return
        with SessionLocal() as db:
            r = db.query(Run).filter(Run.id == run_id).first()
            if not r:
                self.clear_cancel(run_id)
                return
            # If already cancelled by batch cancel while queued, skip execution
            if r.status == "cancelled":
                self.clear_cancel(run_id)
                await event_bus.publish(RunEvent(run_id=run_id, type="finished", payload={"status": "cancelled"}))
                self._sync_batch_counters(run_id)
                return
            t = db.query(TestDefinition).filter(TestDefinition.id == r.test_id).first() if r.test_id else None
            b = db.query(Build).filter(Build.id == r.build_id).first() if r.build_id else None
            if not t:
                r.status = "error"
                r.error_message = "Test definition not found"
                r.finished_at = _utcnow()
                db.commit()
                self.clear_cancel(run_id)
                return

            r.status = "running"
            r.started_at = _utcnow()
            db.commit()

            project_id = r.project_id
            run_dir = ensure_run_dir(settings.artifacts_dir, project_id, r.id)

        await event_bus.publish(RunEvent(run_id=run_id, type="started", payload={"runId": run_id}))

        loop = asyncio.get_running_loop()
        app_path = b.file_path if b else None
        build_meta = b.build_metadata if b else {}
        platform = self._platform_for_run(run_id)
        device_target = self._device_for_run(run_id)
        raw_steps, template_steps, data_context = self._resolve_steps(run_id)

        _result: dict = {}

        def _blocking_work() -> None:
            appium_handle = ensure_appium_running()
            if appium_handle is not None:
                self._managed_appium = appium_handle
            driver: Optional[WebDriver] = None
            rec = None
            ios_rec = None
            artifacts: dict = {"screenshots": [], "pageSources": [], "video": None}

            try:
                raw_driver = create_driver(SessionConfig(platform=platform, device_target=device_target, app_path=app_path, build_meta=build_meta or {}))
                driver = wrap_driver_with_debug(raw_driver, run_id, loop)

                if platform == "android":
                    rec = start_screenrecord(device_target)
                elif platform == "ios_sim":
                    ios_rec = start_ios_recording(device_target, run_dir / "run.mov")

                steps = parse_steps(raw_steps)

                def cancel_check() -> bool:
                    return self.is_cancelled(run_id)

                def on_step(idx, step, status, details):
                    shot = save_screenshot(driver, run_dir, f"step_{idx:03d}_{status}.png")
                    src = save_page_source(driver, run_dir, f"step_{idx:03d}.xml")
                    artifacts["screenshots"].append(shot)
                    artifacts["pageSources"].append(src)
                    asyncio.run_coroutine_threadsafe(
                        event_bus.publish(
                            RunEvent(
                                run_id=run_id,
                                type="step",
                                payload={
                                    "idx": idx,
                                    "step": {"type": step.type, "selector": step.selector.__dict__ if step.selector else None},
                                    "status": status,
                                    "details": details,
                                    "screenshot": shot,
                                    "pageSource": src,
                                },
                            )
                        ),
                        loop,
                    )

                summary = run_steps(driver, steps, on_step=on_step, cancel_check=cancel_check, run_id=run_id)

                if rec:
                    video_name = "run.mp4"
                    ok = stop_and_pull(rec, run_dir / video_name)
                    if ok:
                        artifacts["video"] = video_name
                        postprocess_mp4_for_broad_playback(run_dir / video_name)
                if ios_rec:
                    ok = stop_ios_recording(ios_rec)
                    if ok:
                        mp4_path = run_dir / "run.mp4"
                        if transcode_mov_to_mp4(ios_rec.out_path, mp4_path):
                            try:
                                ios_rec.out_path.unlink(missing_ok=True)
                            except OSError:
                                pass
                            artifacts["video"] = "run.mp4"
                            postprocess_mp4_for_broad_playback(mp4_path)
                        else:
                            artifacts["video"] = ios_rec.out_path.name

                cancelled = self.is_cancelled(run_id)
                self.clear_cancel(run_id)
                verdict = "cancelled" if cancelled else ("passed" if summary.get("failedSteps", 0) == 0 else "failed")
                summary["stepDefinitions"] = raw_steps
                if template_steps:
                    summary["templateSteps"] = template_steps
                if data_context:
                    summary["dataContext"] = data_context

                _result["summary"] = summary
                _result["artifacts"] = artifacts
                _result["verdict"] = verdict
                _result["error"] = None

            except Exception as e:
                _result["summary"] = None
                _result["artifacts"] = artifacts
                _result["verdict"] = "error"
                _result["error"] = str(e)
            finally:
                try:
                    if driver:
                        driver.quit()
                except Exception:
                    pass

        await loop.run_in_executor(None, _blocking_work)

        verdict = _result.get("verdict", "error")
        summary = _result.get("summary")
        artifacts = _result.get("artifacts", {})
        error_str = _result.get("error")

        with SessionLocal() as db:
            r = db.query(Run).filter(Run.id == run_id).first()
            if r:
                # If already force-cancelled by the cancel endpoint, preserve that
                # but still save summary/artifacts for any steps that did complete.
                if r.status == "cancelled":
                    verdict = "cancelled"
                r.status = verdict
                if summary:
                    r.summary = summary
                if artifacts:
                    r.artifacts = artifacts
                r.finished_at = r.finished_at or _utcnow()
                if verdict == "failed":
                    r.failure_category = _classify_error(r.error_message or "")
                elif verdict == "error" and error_str:
                    r.error_message = error_str
                    r.failure_category = _classify_error(error_str)
                db.commit()

        if verdict == "error" and error_str:
            await event_bus.publish(RunEvent(run_id=run_id, type="finished", payload={"status": "error", "error": error_str, "artifacts": artifacts}))
        else:
            await event_bus.publish(RunEvent(run_id=run_id, type="finished", payload={"status": verdict, "summary": summary, "artifacts": artifacts}))

        self.clear_cancel(run_id)
        self._sync_batch_counters(run_id)

    @staticmethod
    def _platform_for_run(run_id: int) -> str:
        with SessionLocal() as db:
            r = db.query(Run).filter(Run.id == run_id).first()
            return (r.platform if r else "android") or "android"

    @staticmethod
    def _device_for_run(run_id: int) -> str:
        with SessionLocal() as db:
            r = db.query(Run).filter(Run.id == run_id).first()
            return (r.device_target if r else "") or ""

    @staticmethod
    def _resolve_steps(run_id: int) -> tuple[list[dict], list[dict], dict[str, str]]:
        """Return (resolved_steps, template_steps, data_context).

        template_steps are the pre-resolution steps with ${var} references.
        data_context is the flattened variable dict used for resolution.
        Both are stored in the run summary so AI Fix can access them.
        """
        import copy
        from .variables import build_context, resolve_step

        with SessionLocal() as db:
            r = db.query(Run).filter(Run.id == run_id).first()
            if not r or not r.test_id:
                return [], [], {}
            t = db.query(TestDefinition).filter(TestDefinition.id == r.test_id).first()
            if not t:
                return [], [], {}

            platform = (r.platform or "android").strip() or "android"

            def _resolve(test: TestDefinition) -> list[dict]:
                ps = getattr(test, "platform_steps", None) or {}
                if isinstance(ps, dict) and platform in ps and ps[platform]:
                    return list(ps[platform])
                return list(test.steps or [])

            steps = _resolve(t)
            if t.prerequisite_test_id and t.prerequisite_test_id != t.id:
                prereq = db.query(TestDefinition).filter(TestDefinition.id == t.prerequisite_test_id).first()
                if prereq:
                    steps = _resolve(prereq) + steps

            template_steps = copy.deepcopy(steps)
            data_context: dict[str, str] = {}

            ds_vars: dict | None = None
            ds_rows: list[dict] | None = None
            data_set_id = getattr(r, "data_set_id", None)
            data_row_index = getattr(r, "data_row_index", None)

            if data_set_id:
                from ..models import DataSet
                ds = db.query(DataSet).filter(DataSet.id == data_set_id).first()
                if ds:
                    ds_vars = ds.variables
                    ds_rows = ds.rows
            elif r.project_id:
                from ..models import DataSet
                default_ds = db.query(DataSet).filter(
                    DataSet.project_id == r.project_id,
                    DataSet.is_default == 1,
                ).first()
                if default_ds:
                    ds_vars = default_ds.variables

            if ds_vars or ds_rows:
                ctx = build_context(
                    data_set_variables=ds_vars,
                    data_set_rows=ds_rows,
                    row_index=data_row_index,
                    run_id=run_id,
                    platform=platform,
                )
                data_context = dict(ctx)
                steps = [resolve_step(s, ctx) for s in steps]

            return steps, template_steps, data_context


run_engine = RunEngine()

