from __future__ import annotations

import asyncio
import time
from typing import Any

from appium.webdriver.common.appiumby import AppiumBy
from appium.webdriver.webdriver import WebDriver
from selenium.webdriver.common.by import By

from ..events import RunEvent, event_bus
from .steps import Step


_CANCELLED = "cancelled"


# ── Utilities ──────────────────────────────────────────────────────────


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
    """Poll for visible element; return _CANCELLED or raise TimeoutError."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if cancel_check and cancel_check():
            return _CANCELLED
        try:
            el = driver.find_element(by, value)
            if el.is_displayed():
                return el
        except Exception:
            pass
        time.sleep(0.25)
    raise TimeoutError(f"Element not visible within {timeout_sec}s")


def _poll_until_not_visible(
    driver: WebDriver,
    by: str,
    value: str,
    timeout_sec: float,
    cancel_check: callable | None,
) -> str | None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if cancel_check and cancel_check():
            return _CANCELLED
        try:
            el = driver.find_element(by, value)
            if not el.is_displayed():
                return None
        except Exception:
            return None
        time.sleep(0.25)
    raise TimeoutError(f"Element still visible after {timeout_sec}s")


def _poll_until_enabled(
    driver: WebDriver,
    by: str,
    value: str,
    timeout_sec: float,
    cancel_check: callable | None,
    want_enabled: bool = True,
) -> Any:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if cancel_check and cancel_check():
            return _CANCELLED
        try:
            el = driver.find_element(by, value)
            if el.is_enabled() == want_enabled:
                return el
        except Exception:
            pass
        time.sleep(0.25)
    state = "enabled" if want_enabled else "disabled"
    raise TimeoutError(f"Element did not become {state} within {timeout_sec}s")


def _debug(run_id: int | None, name: str, category: str, **kwargs: Any) -> None:
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
    norm = " ".join((selector_using or "").strip().lower().split())
    norm_ns = norm.replace(" ", "")
    if norm in ("-android uiautomator", "android uiautomator") or norm_ns in (
        "-androiduiautomator",
        "androiduiautomator",
    ) or norm == "uiautomator":
        return AppiumBy.ANDROID_UIAUTOMATOR
    if selector_using == "xpath":
        return By.XPATH
    if selector_using == "id":
        return By.ID
    if selector_using in ("accessibilityId", "accessibility id"):
        return "accessibility id"
    if selector_using == "className":
        return By.CLASS_NAME
    if norm in ("-ios predicate string", "ios predicate string") or norm_ns in (
        "-iospredicatestring",
        "iospredicatestring",
    ):
        return AppiumBy.IOS_PREDICATE
    if norm in ("-ios class chain", "ios class chain") or norm_ns in ("-iosclasschain", "iosclasschain"):
        return AppiumBy.IOS_CLASS_CHAIN
    raise ValueError(f"Unknown selector strategy: {selector_using}")


def _is_keyboard_target(step: Step) -> bool:
    return bool(step.selector and step.selector.value.lower() in _KEYBOARD_KEYS)


def _find_with_cancel(
    driver: WebDriver,
    by: str,
    value: str,
    cancel_check: callable | None,
    timeout_sec: float = 15.0,
) -> Any:
    """Poll find_element; return element, _CANCELLED, or raise TimeoutError."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if cancel_check and cancel_check():
            return _CANCELLED
        try:
            return driver.find_element(by, value)
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"Element not found within {timeout_sec}s: {value}")


def _require_selector(step: Step) -> None:
    if not step.selector:
        raise ValueError(f"{step.type} requires a selector")


def _resolve(step: Step) -> tuple[str, str]:
    """Return (appium_by, value) from step selector."""
    _require_selector(step)
    return _by(step.selector.using), step.selector.value


def _find(driver: WebDriver, step: Step, cancel_check: callable | None, timeout: float = 15.0) -> Any:
    by, value = _resolve(step)
    return _find_with_cancel(driver, by, value, cancel_check, timeout)


# ── Handler context ────────────────────────────────────────────────────
# Every handler receives (driver, step, cancel_check, run_id) and returns
# _CANCELLED to break the loop, or None on success. Raises on failure.


# ── Visibility & Waiting ──────────────────────────────────────────────


def _handle_wait(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    if not _sleep_cancellable(float(step.ms or 500), cancel_check):
        return _CANCELLED
    return None


def _handle_wait_for_visible(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    timeout = (step.ms or 10000) / 1000.0
    by, value = _resolve(step)
    result = _poll_until_visible(driver, by, value, timeout, cancel_check)
    return _CANCELLED if result == _CANCELLED else None


def _handle_wait_for_not_visible(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    timeout = (step.ms or 10000) / 1000.0
    by, value = _resolve(step)
    result = _poll_until_not_visible(driver, by, value, timeout, cancel_check)
    return _CANCELLED if result == _CANCELLED else None


def _handle_wait_for_enabled(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    timeout = (step.ms or 10000) / 1000.0
    by, value = _resolve(step)
    result = _poll_until_enabled(driver, by, value, timeout, cancel_check, want_enabled=True)
    return _CANCELLED if result == _CANCELLED else None


def _handle_wait_for_disabled(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    timeout = (step.ms or 10000) / 1000.0
    by, value = _resolve(step)
    result = _poll_until_enabled(driver, by, value, timeout, cancel_check, want_enabled=False)
    return _CANCELLED if result == _CANCELLED else None


# ── Tapping & Gestures ────────────────────────────────────────────────


def _handle_tap(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    if _is_keyboard_target(step):
        key = step.selector.value.lower()
        try:
            driver.execute_script("mobile: pressButton", {"name": key})
        except Exception:
            try:
                driver.hide_keyboard()
            except Exception:
                el = _find(driver, step, cancel_check, 15.0)
                if el == _CANCELLED:
                    return _CANCELLED
                el.click()
    else:
        el = _find(driver, step, cancel_check, 15.0)
        if el == _CANCELLED:
            return _CANCELLED
        el.click()
    return None


def _handle_double_tap(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    el = _find(driver, step, cancel_check, 15.0)
    if el == _CANCELLED:
        return _CANCELLED
    _debug(run_id, "double_tap", "action")
    try:
        driver.execute_script("mobile: doubleTap", {"element": el.id})
    except Exception:
        from appium.webdriver.common.touch_action import TouchAction
        TouchAction(driver).tap(el, count=2).perform()
    return None


def _handle_long_press(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    el = _find(driver, step, cancel_check, 15.0)
    if el == _CANCELLED:
        return _CANCELLED
    duration = int(step.ms or 2000)
    _debug(run_id, "long_press", "action", duration_ms=duration)
    try:
        driver.execute_script("mobile: longClickGesture", {"elementId": el.id, "duration": duration})
    except Exception:
        from appium.webdriver.common.touch_action import TouchAction
        TouchAction(driver).long_press(el, duration=duration).release().perform()
    return None


def _handle_tap_by_coordinates(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    meta = step.meta or {}
    x = int(meta.get("x", 0))
    y = int(meta.get("y", 0))
    if x == 0 and y == 0:
        raise ValueError("tapByCoordinates requires meta.x and meta.y")
    _debug(run_id, "tap_by_coordinates", "action", x=x, y=y)
    try:
        driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
    except Exception:
        from appium.webdriver.common.touch_action import TouchAction
        TouchAction(driver).tap(x=x, y=y).perform()
    return None


def _handle_swipe(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
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
    return None


def _handle_scroll(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    direction = (step.text or "down").lower()
    _debug(run_id, "scroll", "action", direction=direction)
    if step.selector:
        by, value = _resolve(step)
        timeout = (step.ms or 15000) / 1000.0
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cancel_check and cancel_check():
                return _CANCELLED
            try:
                el = driver.find_element(by, value)
                if el.is_displayed():
                    return None
            except Exception:
                pass
            try:
                driver.execute_script("mobile: scroll", {"direction": direction})
            except Exception:
                try:
                    driver.execute_script("mobile: swipe", {"direction": direction})
                except Exception:
                    pass
            time.sleep(0.3)
        raise TimeoutError(f"Element not found after scrolling {direction} for {timeout}s")
    else:
        try:
            driver.execute_script("mobile: scroll", {"direction": direction})
        except Exception:
            driver.execute_script("mobile: swipe", {"direction": direction})
    return None


# ── Text Input ────────────────────────────────────────────────────────


def _handle_type(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    el = _find(driver, step, cancel_check, 15.0)
    if el == _CANCELLED:
        return _CANCELLED
    el.clear()
    el.send_keys(step.text or "")
    return None


def _handle_clear(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    el = _find(driver, step, cancel_check, 15.0)
    if el == _CANCELLED:
        return _CANCELLED
    el.clear()
    return None


def _handle_clear_and_type(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    el = _find(driver, step, cancel_check, 15.0)
    if el == _CANCELLED:
        return _CANCELLED
    el.clear()
    el.send_keys(step.text or "")
    return None


def _handle_hide_keyboard(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _debug(run_id, "hide_keyboard", "action")
    try:
        driver.hide_keyboard()
    except Exception:
        pass
    return None


# ── Assertions ────────────────────────────────────────────────────────


def _handle_assert_text(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    el = _find(driver, step, cancel_check, 15.0)
    if el == _CANCELLED:
        return _CANCELLED
    actual = el.text or ""
    expected = step.expect or ""
    if expected not in actual:
        raise AssertionError(f"Expected '{expected}' to be in '{actual}'")
    return None


def _handle_assert_text_contains(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    el = _find(driver, step, cancel_check, 15.0)
    if el == _CANCELLED:
        return _CANCELLED
    actual = el.text or ""
    expected = step.expect or ""
    if expected.lower() not in actual.lower():
        raise AssertionError(f"Expected '{actual}' to contain '{expected}'")
    return None


def _handle_assert_visible(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    el = _find(driver, step, cancel_check, 15.0)
    if el == _CANCELLED:
        return _CANCELLED
    if not el.is_displayed():
        raise AssertionError(f"Element '{step.selector.value}' exists but is not visible")
    return None


def _handle_assert_not_visible(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    by, value = _resolve(step)
    try:
        el = driver.find_element(by, value)
        if el.is_displayed():
            raise AssertionError(f"Element '{value}' is visible but should not be")
    except AssertionError:
        raise
    except Exception:
        pass
    return None


def _handle_assert_enabled(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    el = _find(driver, step, cancel_check, 15.0)
    if el == _CANCELLED:
        return _CANCELLED
    if not el.is_enabled():
        raise AssertionError(f"Element '{step.selector.value}' is not enabled")
    return None


def _handle_assert_checked(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    el = _find(driver, step, cancel_check, 15.0)
    if el == _CANCELLED:
        return _CANCELLED
    checked = el.get_attribute("checked")
    if str(checked).lower() not in ("true", "1"):
        raise AssertionError(f"Element '{step.selector.value}' is not checked (checked={checked})")
    return None


def _handle_assert_attribute(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _require_selector(step)
    meta = step.meta or {}
    attr_name = meta.get("attribute", "")
    if not attr_name:
        raise ValueError("assertAttribute requires meta.attribute")
    expected = step.expect or ""
    el = _find(driver, step, cancel_check, 15.0)
    if el == _CANCELLED:
        return _CANCELLED
    actual = el.get_attribute(attr_name) or ""
    if actual != expected:
        raise AssertionError(f"Attribute '{attr_name}': expected '{expected}', got '{actual}'")
    return None


# ── Navigation & System ───────────────────────────────────────────────


def _handle_press_key(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    key = (step.text or (step.meta or {}).get("action", "done")).lower()
    _debug(run_id, "press_key", "action", key=key)
    _KEY_MAP = {
        "back": 4, "home": 3, "enter": 66, "return": 66,
        "delete": 67, "backspace": 67, "tab": 61, "menu": 82,
    }
    android_code = _KEY_MAP.get(key)
    if android_code:
        try:
            driver.press_keycode(android_code)
            return None
        except Exception:
            pass
    try:
        driver.execute_script("mobile: pressButton", {"name": key})
    except Exception:
        try:
            driver.execute_script("mobile: performEditorAction", {"action": key})
        except Exception:
            driver.hide_keyboard()
    return None


def _handle_keyboard_action(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    """Legacy alias — routes to pressKey."""
    return _handle_press_key(driver, step, cancel_check, run_id)


def _handle_launch_app(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    bundle = (step.text or (step.meta or {}).get("bundleId", "")).strip()
    _debug(run_id, "launch_app", "action", bundle=bundle)
    if bundle:
        driver.execute_script("mobile: activateApp", {"bundleId": bundle})
    else:
        try:
            driver.execute_script("mobile: activateApp", {"bundleId": driver.capabilities.get("appPackage") or driver.capabilities.get("bundleId", "")})
        except Exception:
            driver.activate_app(driver.capabilities.get("appPackage") or driver.capabilities.get("bundleId", ""))
    return None


def _handle_close_app(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    bundle = (step.text or (step.meta or {}).get("bundleId", "")).strip()
    _debug(run_id, "close_app", "action", bundle=bundle)
    pkg = bundle or driver.capabilities.get("appPackage") or driver.capabilities.get("bundleId", "")
    if pkg:
        try:
            driver.execute_script("mobile: terminateApp", {"bundleId": pkg})
        except Exception:
            driver.terminate_app(pkg)
    return None


def _handle_reset_app(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _debug(run_id, "reset_app", "action")
    pkg = (step.text or (step.meta or {}).get("bundleId", "")).strip() or driver.capabilities.get("appPackage") or driver.capabilities.get("bundleId", "")
    if pkg:
        try:
            driver.execute_script("mobile: terminateApp", {"bundleId": pkg})
        except Exception:
            pass
        time.sleep(0.5)
        try:
            driver.execute_script("mobile: activateApp", {"bundleId": pkg})
        except Exception:
            driver.activate_app(pkg)
    return None


# ── Capture & Verification ────────────────────────────────────────────


def _handle_screenshot(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _debug(run_id, "take_screenshot", "capture")
    return None


def _handle_get_page_source(driver: WebDriver, step: Step, cancel_check, run_id) -> str | None:
    _debug(run_id, "get_page_source", "capture")
    driver.page_source  # noqa: B018 — captured by on_step callback
    return None


# ── Handler Registry ──────────────────────────────────────────────────


STEP_HANDLERS: dict[str, callable] = {
    # Visibility & Waiting
    "wait":               _handle_wait,
    "waitForVisible":     _handle_wait_for_visible,
    "waitForNotVisible":  _handle_wait_for_not_visible,
    "waitForEnabled":     _handle_wait_for_enabled,
    "waitForDisabled":    _handle_wait_for_disabled,
    # Tapping & Gestures
    "tap":                _handle_tap,
    "doubleTap":          _handle_double_tap,
    "longPress":          _handle_long_press,
    "tapByCoordinates":   _handle_tap_by_coordinates,
    "swipe":              _handle_swipe,
    "scroll":             _handle_scroll,
    # Text Input
    "type":               _handle_type,
    "clear":              _handle_clear,
    "clearAndType":       _handle_clear_and_type,
    "hideKeyboard":       _handle_hide_keyboard,
    # Assertions
    "assertText":         _handle_assert_text,
    "assertTextContains": _handle_assert_text_contains,
    "assertVisible":      _handle_assert_visible,
    "assertNotVisible":   _handle_assert_not_visible,
    "assertEnabled":      _handle_assert_enabled,
    "assertChecked":      _handle_assert_checked,
    "assertAttribute":    _handle_assert_attribute,
    # Navigation & System
    "pressKey":           _handle_press_key,
    "keyboardAction":     _handle_keyboard_action,
    "launchApp":          _handle_launch_app,
    "closeApp":           _handle_close_app,
    "resetApp":           _handle_reset_app,
    # Capture
    "screenshot":         _handle_screenshot,
    "takeScreenshot":     _handle_screenshot,
    "getPageSource":      _handle_get_page_source,
}

ASSERT_TYPES = frozenset({
    "assertText", "assertTextContains", "assertVisible", "assertNotVisible",
    "assertEnabled", "assertChecked", "assertAttribute",
})


# ── Run Loop ──────────────────────────────────────────────────────────


def run_steps(
    driver: WebDriver,
    steps: list[Step],
    on_step: callable,
    cancel_check: callable | None = None,
    run_id: int | None = None,
    on_step_start: callable | None = None,
) -> dict[str, Any]:
    passed = 0
    failed = 0
    step_results: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for idx, step in enumerate(steps):
        if cancel_check and cancel_check():
            break
        if on_step_start:
            on_step_start(idx)
        t0 = time.time()
        try:
            handler = STEP_HANDLERS.get(step.type)
            if not handler:
                raise ValueError(
                    f"Unknown step type: '{step.type}'. "
                    f"Supported: {sorted(STEP_HANDLERS.keys())}"
                )
            result = handler(driver, step, cancel_check, run_id)
            if result == _CANCELLED:
                break

            passed += 1
            details = {"durationMs": int((time.time() - t0) * 1000)}
            step_results.append({"idx": idx, "type": step.type, "status": "passed", "details": details})
            if step.type in ASSERT_TYPES:
                events.append({
                    "step": idx, "type": step.type,
                    "name": step.selector.value if step.selector else "",
                    "status": "passed", "ts": time.time(),
                })
            on_step(idx, step, "passed", details)

        except Exception as e:
            failed += 1
            details = {"durationMs": int((time.time() - t0) * 1000), "error": str(e)}
            step_results.append({"idx": idx, "type": step.type, "status": "failed", "details": details})
            if step.type in ASSERT_TYPES:
                events.append({
                    "step": idx, "type": step.type,
                    "name": step.selector.value if step.selector else "",
                    "status": "failed", "error": str(e), "ts": time.time(),
                })
            on_step(idx, step, "failed", details)
            break

    return {
        "totalSteps": len(steps),
        "passedSteps": passed,
        "failedSteps": failed,
        "stepResults": step_results,
        "events": events,
    }
