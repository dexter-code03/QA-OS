from __future__ import annotations

import time
from typing import Any

from appium.webdriver.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from .steps import Step

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


def run_steps(driver: WebDriver, steps: list[Step], on_step: callable) -> dict[str, Any]:
    passed = 0
    failed = 0
    step_results: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for idx, step in enumerate(steps):
        t0 = time.time()
        try:
            if step.type == "wait":
                time.sleep((step.ms or 500) / 1000.0)

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
                            el = driver.find_element(_by(step.selector.using), step.selector.value)
                            el.click()
                else:
                    el = driver.find_element(_by(step.selector.using), step.selector.value)
                    el.click()

            elif step.type == "type":
                if not step.selector:
                    raise ValueError("type requires selector")
                el = driver.find_element(_by(step.selector.using), step.selector.value)
                el.clear()
                el.send_keys(step.text or "")

            elif step.type == "waitForVisible":
                if not step.selector:
                    raise ValueError("waitForVisible requires selector")
                timeout = (step.ms or 10000) / 1000.0
                WebDriverWait(driver, timeout).until(
                    EC.visibility_of_element_located((_by(step.selector.using), step.selector.value))
                )

            elif step.type == "assertText":
                if not step.selector:
                    raise ValueError("assertText requires selector")
                el = driver.find_element(_by(step.selector.using), step.selector.value)
                actual = el.text or ""
                expected = step.expect or ""
                if expected not in actual:
                    raise AssertionError(f"Expected '{expected}' to be in '{actual}'")
                events.append({"step": idx, "type": "assertText", "name": step.selector.value, "expected": expected, "actual": actual, "status": "passed", "ts": time.time()})

            elif step.type == "assertVisible":
                if not step.selector:
                    raise ValueError("assertVisible requires selector")
                el = driver.find_element(_by(step.selector.using), step.selector.value)
                if not el.is_displayed():
                    raise AssertionError(f"Element '{step.selector.value}' exists but is not visible")
                events.append({"step": idx, "type": "assertVisible", "name": step.selector.value, "status": "passed", "ts": time.time()})

            elif step.type == "keyboardAction":
                action = (step.text or step.meta.get("action", "done") if step.meta else "done").lower()
                try:
                    driver.execute_script("mobile: pressButton", {"name": action})
                except Exception:
                    try:
                        driver.execute_script("mobile: performEditorAction", {"action": action})
                    except Exception:
                        driver.hide_keyboard()

            elif step.type == "hideKeyboard":
                try:
                    driver.hide_keyboard()
                except Exception:
                    pass

            elif step.type == "swipe":
                direction = (step.text or "up").lower()
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
                pass

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

