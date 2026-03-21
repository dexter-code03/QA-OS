from __future__ import annotations

import asyncio
import time
from typing import Any

from selenium.webdriver.support.events import AbstractEventListener, EventFiringWebDriver

from ..events import RunEvent, event_bus


class DebugEventListener(AbstractEventListener):
    """Publishes granular Appium events to the run event bus."""

    def __init__(self, run_id: int, loop: asyncio.AbstractEventLoop) -> None:
        self.run_id = run_id
        self._loop = loop
        self._find_start: list[float] = []

    def _publish(self, name: str, category: str, payload: dict[str, Any]) -> None:
        payload["ts"] = round(time.time() * 1000)
        asyncio.run_coroutine_threadsafe(
            event_bus.publish(
                RunEvent(
                    run_id=self.run_id,
                    type="debug",
                    payload={"name": name, "category": category, **payload},
                )
            ),
            self._loop,
        )

    # ── Element finding ──────────────────────────────
    def before_find(self, by, value, driver):
        self._find_start.append(time.time())
        self._publish("before_find", "find", {"strategy": str(by), "value": str(value)[:200]})

    def after_find(self, by, value, driver):
        t0 = self._find_start.pop() if self._find_start else time.time()
        dur = round((time.time() - t0) * 1000)
        self._publish(
            "after_find",
            "find",
            {"strategy": str(by), "value": str(value)[:200], "found": True, "dur": dur},
        )

    def on_exception(self, exception, driver):
        self._publish(
            "exception",
            "error",
            {"type": type(exception).__name__, "message": str(exception)[:400]},
        )

    # ── Element interactions ─────────────────────────
    def before_click(self, element, driver):
        tag = element.tag_name if element else ""
        text = (element.text or "")[:80] if element else ""
        self._publish("before_click", "action", {"tag": tag, "text": text})

    def after_click(self, element, driver):
        self._publish("after_click", "action", {"success": True})

    def before_change_value_of(self, element, driver):
        tag = element.tag_name if element else ""
        self._publish("before_send_keys", "action", {"tag": tag})

    def after_change_value_of(self, element, driver):
        self._publish("after_send_keys", "action", {"success": True})

    # ── Script execution (mobile: commands) ──────────
    def before_execute_script(self, script, driver):
        self._publish("before_script", "action", {"script": str(script)[:100]})

    def after_execute_script(self, script, driver):
        self._publish("after_script", "action", {"success": True})


def wrap_driver_with_debug(driver, run_id: int, loop: asyncio.AbstractEventLoop):
    """Wrap an Appium WebDriver with the debug event listener."""
    listener = DebugEventListener(run_id, loop)
    return EventFiringWebDriver(driver, listener)
