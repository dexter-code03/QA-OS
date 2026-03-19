from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from appium.webdriver.webdriver import WebDriver

from ..db import SessionLocal, Run, TestDefinition, Build
from ..events import RunEvent, event_bus
from ..settings import settings
from .appium_service import ensure_appium_running
from .artifacts import ensure_run_dir, save_page_source, save_screenshot
from .executor import run_steps
from .recording_android import start_screenrecord, stop_and_pull
from .recording_ios_sim import start_recording as start_ios_recording, stop as stop_ios_recording
from .session import SessionConfig, create_driver
from .steps import parse_steps


@dataclass(frozen=True)
class EnqueuedRun:
    run_id: int


class RunEngine:
    def __init__(self) -> None:
        self._q: asyncio.Queue[EnqueuedRun] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._cancel_requested: set[int] = set()

    def request_cancel(self, run_id: int) -> None:
        self._cancel_requested.add(run_id)

    def is_cancelled(self, run_id: int) -> bool:
        return run_id in self._cancel_requested

    def clear_cancel(self, run_id: int) -> None:
        self._cancel_requested.discard(run_id)

    def start(self) -> None:
        if self._task:
            return
        self._task = asyncio.create_task(self._loop())

    async def enqueue(self, run_id: int) -> None:
        await self._q.put(EnqueuedRun(run_id=run_id))

    async def _loop(self) -> None:
        while True:
            try:
                item = await asyncio.wait_for(self._q.get(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            try:
                await self._execute(item.run_id)
            except Exception as e:
                await event_bus.publish(RunEvent(run_id=item.run_id, type="engine_error", payload={"error": str(e)}))

    async def _execute(self, run_id: int) -> None:
        if self.is_cancelled(run_id):
            with SessionLocal() as db:
                r = db.query(Run).filter(Run.id == run_id).first()
                if r:
                    r.status = "cancelled"
                    r.finished_at = datetime.utcnow()
                    db.commit()
            self.clear_cancel(run_id)
            await event_bus.publish(RunEvent(run_id=run_id, type="finished", payload={"status": "cancelled"}))
            return
        with SessionLocal() as db:
            r = db.query(Run).filter(Run.id == run_id).first()
            if not r:
                return
            t = db.query(TestDefinition).filter(TestDefinition.id == r.test_id).first() if r.test_id else None
            b = db.query(Build).filter(Build.id == r.build_id).first() if r.build_id else None
            if not t:
                r.status = "error"
                r.error_message = "Test definition not found"
                r.finished_at = datetime.utcnow()
                db.commit()
                return

            r.status = "running"
            r.started_at = datetime.utcnow()
            db.commit()

            project_id = r.project_id
            run_dir = ensure_run_dir(settings.artifacts_dir, project_id, r.id)

        await event_bus.publish(RunEvent(run_id=run_id, type="started", payload={"runId": run_id}))

        appium_handle = ensure_appium_running()
        driver: Optional[WebDriver] = None
        rec = None
        ios_rec = None
        artifacts: dict = {"screenshots": [], "pageSources": [], "video": None}

        try:
            app_path = b.file_path if b else None
            build_meta = b.build_metadata if b else {}
            platform = self._platform_for_run(run_id)
            device_target = self._device_for_run(run_id)
            driver = create_driver(SessionConfig(platform=platform, device_target=device_target, app_path=app_path, build_meta=build_meta or {}))

            if platform == "android":
                rec = start_screenrecord(device_target)
            elif platform == "ios_sim":
                ios_rec = start_ios_recording(device_target, run_dir / "run.mov")

            raw_steps = self._steps_for_run(run_id)
            steps = parse_steps(raw_steps)

            def cancel_check() -> bool:
                return self.is_cancelled(run_id)

            def on_step(idx, step, status, details):
                shot = save_screenshot(driver, run_dir, f"step_{idx:03d}_{status}.png")
                src = save_page_source(driver, run_dir, f"step_{idx:03d}.xml")
                artifacts["screenshots"].append(shot)
                artifacts["pageSources"].append(src)
                asyncio.create_task(
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
                    )
                )

            summary = run_steps(driver, steps, on_step=on_step, cancel_check=cancel_check)

            # stop recording
            if rec:
                video_name = "run.mp4"
                ok = stop_and_pull(rec, run_dir / video_name)
                if ok:
                    artifacts["video"] = video_name
            if ios_rec:
                ok = stop_ios_recording(ios_rec)
                if ok:
                    artifacts["video"] = ios_rec.out_path.name

            cancelled = self.is_cancelled(run_id)
            self.clear_cancel(run_id)
            verdict = "cancelled" if cancelled else ("passed" if summary.get("failedSteps", 0) == 0 else "failed")
            summary["stepDefinitions"] = raw_steps
            with SessionLocal() as db:
                r = db.query(Run).filter(Run.id == run_id).first()
                if r:
                    r.status = verdict
                    r.summary = summary
                    r.artifacts = artifacts
                    r.finished_at = datetime.utcnow()
                    db.commit()

            await event_bus.publish(RunEvent(run_id=run_id, type="finished", payload={"status": verdict, "summary": summary, "artifacts": artifacts}))

        except Exception as e:
            with SessionLocal() as db:
                r = db.query(Run).filter(Run.id == run_id).first()
                if r:
                    r.status = "error"
                    r.error_message = str(e)
                    r.artifacts = artifacts
                    r.finished_at = datetime.utcnow()
                    db.commit()
            await event_bus.publish(RunEvent(run_id=run_id, type="finished", payload={"status": "error", "error": str(e), "artifacts": artifacts}))
        finally:
            self.clear_cancel(run_id)
            try:
                if driver:
                    driver.quit()
            except Exception:
                pass
            try:
                if appium_handle and appium_handle.process:
                    appium_handle.process.terminate()
            except Exception:
                pass

    def _platform_for_run(self, run_id: int) -> str:
        with SessionLocal() as db:
            r = db.query(Run).filter(Run.id == run_id).first()
            return (r.platform if r else "android") or "android"

    def _device_for_run(self, run_id: int) -> str:
        with SessionLocal() as db:
            r = db.query(Run).filter(Run.id == run_id).first()
            return (r.device_target if r else "") or ""

    def _steps_for_run(self, run_id: int) -> list[dict]:
        with SessionLocal() as db:
            r = db.query(Run).filter(Run.id == run_id).first()
            if not r or not r.test_id:
                return []
            t = db.query(TestDefinition).filter(TestDefinition.id == r.test_id).first()
            if not t:
                return []
            steps = list(t.steps or [])
            if t.prerequisite_test_id and t.prerequisite_test_id != t.id:
                prereq = db.query(TestDefinition).filter(TestDefinition.id == t.prerequisite_test_id).first()
                if prereq and prereq.steps:
                    steps = list(prereq.steps) + steps
            return steps


run_engine = RunEngine()

