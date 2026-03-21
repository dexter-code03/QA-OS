from __future__ import annotations

import asyncio
import time
from typing import Any

from appium.webdriver.webdriver import WebDriver
from selenium.webdriver.common.by import By

from ..events import RunEvent, event_bus
from .steps import Step


def _sleep_cancellable(ms: float, cancel_check: callable | None) -> bool:
    """Sleep in chunks; return False if cancelled."""
    remaining = ms / 1000.0
    while remaining > 0:
        if cancel_check and cancel_check():
            return False
        chunk = min(0.25, remaining)
        time.sleep(chunk)
        remaining -= chunk
    return True


def _poll_until_visible(
    driver: WebDriver,
    by: str,
    value: str,
    timeout_sec: float,
    cancel_check: callable | None,
) -> Any:
    """Poll for visible element; return 'cancelled' or raise TimeoutError."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if cancel_check and cancel_check():
            return "cancelled"
        try:
            el = driver.find_element(by, value)
            if el.is_displayed():
                return el
        except Exception:
            pass
        time.sleep(0.25)
    raise TimeoutError(f"Element not visible within {timeout_sec}s")


def _debug(run_id: int | None, name: str, category: str, **kwargs: Any) -> None:
    """Fire a debug event from within synchronous executor code."""
    if run_id is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    payload = {"name": name, "category": category, "ts": round(time.time() * 1000), **kwargs}
    asyncio.run_coroutine_threadsafe(
        event_bus.publish(RunEvent(run_id=run_id, type="debug", payload=payload)),
        loop,
    )

_KEYBOARD_KEYS = {"return", "done", "go", "next", "search", "send", "enter"}


def _by(selector_using: str) -> str:
    if selector_using == "xpath":
        return By.XPATH
    if selector_using == "id":
        return By.ID
    if selector_using in ("accessibilityId", "accessibility id"):
        return "accessibility id"
    if selector_using == "className":
        return By.CLASS_NAME
    raise ValueError(f"Unknown selector strategy: {selector_using}")


def _is_keyboard_target(step: Step) -> bool:
    if step.selector and step.selector.value.lower() in _KEYBOARD_KEYS:
        return True
    return False


def _find_with_cancel(
    driver: WebDriver,
    by: str,
    value: str,
    cancel_check: callable | None,
    timeout_sec: float = 15.0,
) -> Any:
    """Poll find_element; return element, or 'cancelled', or raise TimeoutError."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if cancel_check and cancel_check():
            return "cancelled"
        try:
            return driver.find_element(by, value)
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"Element not found within {timeout_sec}s: {value}")


def run_steps(driver: WebDriver, steps: list[Step], on_step: callable, cancel_check: callable | None = None, run_id: int | None = None) -> dict[str, Any]:
    passed = 0
    failed = 0
    step_results: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for idx, step in enumerate(steps):
        if cancel_check and cancel_check():
            break
        t0 = time.time()
        try:
            if step.type == "wait":
                if not _sleep_cancellable(float(step.ms or 500), cancel_check):
                    break

            elif step.type == "tap":
                if not step.selector:
                    raise ValueError("tap requires selector")
                if _is_keyboard_target(step):
                    key = step.selector.value.lower()
                    try:
                        driver.execute_script("mobile: pressButton", {"name": key})
                    except Exception:
                        try:
                            driver.hide_keyboard()
                        except Exception:
                            el = _find_with_cancel(driver, _by(step.selector.using), step.selector.value, cancel_check, 15.0)
                            if el == "cancelled":
                                break
                            el.click()
                else:
                    el = _find_with_cancel(driver, _by(step.selector.using), step.selector.value, cancel_check, 15.0)
                    if el == "cancelled":
                        break
                    el.click()

            elif step.type == "type":
                if not step.selector:
                    raise ValueError("type requires selector")
                el = _find_with_cancel(driver, _by(step.selector.using), step.selector.value, cancel_check, 15.0)
                if el == "cancelled":
                    break
                el.clear()
                el.send_keys(step.text or "")

            elif step.type == "waitForVisible":
                if not step.selector:
                    raise ValueError("waitForVisible requires selector")
                timeout = (step.ms or 10000) / 1000.0
                vis = _poll_until_visible(driver, _by(step.selector.using), step.selector.value, timeout, cancel_check)
                if vis == "cancelled":
                    break

            elif step.type == "assertText":
                if not step.selector:
                    raise ValueError("assertText requires selector")
                el = _find_with_cancel(driver, _by(step.selector.using), step.selector.value, cancel_check, 15.0)
                if el == "cancelled":
                    break
                actual = el.text or ""
                expected = step.expect or ""
                if expected not in actual:
                    raise AssertionError(f"Expected '{expected}' to be in '{actual}'")
                events.append({"step": idx, "type": "assertText", "name": step.selector.value, "expected": expected, "actual": actual, "status": "passed", "ts": time.time()})

            elif step.type == "assertVisible":
                if not step.selector:
                    raise ValueError("assertVisible requires selector")
                el = _find_with_cancel(driver, _by(step.selector.using), step.selector.value, cancel_check, 15.0)
                if el == "cancelled":
                    break
                if not el.is_displayed():
                    raise AssertionError(f"Element '{step.selector.value}' exists but is not visible")
                events.append({"step": idx, "type": "assertVisible", "name": step.selector.value, "status": "passed", "ts": time.time()})

            elif step.type == "keyboardAction":
                action = (step.text or step.meta.get("action", "done") if step.meta else "done").lower()
                _debug(run_id, "keyboard_action", "action", key=action, method="mobile:pressButton")
                try:
                    driver.execute_script("mobile: pressButton", {"name": action})
                except Exception:
                    try:
                        driver.execute_script("mobile: performEditorAction", {"action": action})
                    except Exception:
                        driver.hide_keyboard()

            elif step.type == "hideKeyboard":
                _debug(run_id, "hide_keyboard", "action")
                try:
                    driver.hide_keyboard()
                except Exception:
                    pass

            elif step.type == "swipe":
                direction = (step.text or "up").lower()
                _debug(run_id, "swipe", "action", direction=direction)
                try:
                    driver.execute_script("mobile: swipe", {"direction": direction})
                except Exception:
                    size = driver.get_window_size()
                    cx, cy = size["width"] // 2, size["height"] // 2
                    offsets = {"up": (0, -300), "down": (0, 300), "left": (-300, 0), "right": (300, 0)}
                    dx, dy = offsets.get(direction, (0, -300))
                    from appium.webdriver.common.touch_action import TouchAction
                    TouchAction(driver).press(x=cx, y=cy).move_to(x=cx + dx, y=cy + dy).release().perform()

            elif step.type == "takeScreenshot":
                _debug(run_id, "take_screenshot", "capture")

            else:
                raise ValueError(f"Unknown step type: {step.type}")

            passed += 1
            details = {"durationMs": int((time.time() - t0) * 1000)}
            step_results.append({"idx": idx, "type": step.type, "status": "passed", "details": details})
            on_step(idx, step, "passed", details)

        except Exception as e:
            failed += 1
            details = {"durationMs": int((time.time() - t0) * 1000), "error": str(e)}
            step_results.append({"idx": idx, "type": step.type, "status": "failed", "details": details})
            if step.type in ("assertText", "assertVisible"):
                events.append({"step": idx, "type": step.type, "name": step.selector.value if step.selector else "", "status": "failed", "error": str(e), "ts": time.time()})
            on_step(idx, step, "failed", details)
            break

    total = len(steps)
    return {
        "totalSteps": total,
        "passedSteps": passed,
        "failedSteps": failed,
        "stepResults": step_results,
        "events": events,
    }

