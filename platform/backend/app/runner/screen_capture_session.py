"""
In-memory Appium WebDriver registry for Screen Library: one long-lived session per
(project, platform, device, build) until Stop or process restart.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TypeVar


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


_registry_lock = threading.Lock()
# key -> slot with per-session lock for driver I/O
_slots: dict[str, dict[str, Any]] = {}

T = TypeVar("T")


def make_session_key(project_id: int, platform: str, device_target: str, build_id: int) -> str:
    return f"{int(project_id)}:{platform}:{device_target}:{int(build_id)}"


def _ensure_slot(key: str) -> dict[str, Any]:
    with _registry_lock:
        if key not in _slots:
            _slots[key] = {
                "lock": threading.Lock(),
                "driver": None,
                "created_at": None,
                "last_used": None,
            }
        return _slots[key]


def get_slot_for_status(key: str) -> Optional[dict[str, Any]]:
    with _registry_lock:
        s = _slots.get(key)
        if not s:
            return None
        return {
            "has_driver": s.get("driver") is not None,
            "created_at": s.get("created_at"),
            "last_used": s.get("last_used"),
        }


def session_active_and_alive(key: str) -> bool:
    """True only if a slot exists with a live driver (does not create a slot)."""
    with _registry_lock:
        slot = _slots.get(key)
    if not slot:
        return False
    with slot["lock"]:
        d = slot.get("driver")
        return bool(d and _driver_alive(d))


def _driver_alive(driver: Any) -> bool:
    try:
        _ = driver.session_id
        return True
    except Exception:
        return False


def with_session_driver(key: str, fn: Callable[[Any], T]) -> T:
    """
    Run fn(driver) under the session lock. Raises RuntimeError if no session or dead driver.
    """
    slot = _ensure_slot(key)
    with slot["lock"]:
        driver = slot.get("driver")
        if not driver or not _driver_alive(driver):
            slot["driver"] = None
            raise RuntimeError("no_active_session")
        try:
            out = fn(driver)
        finally:
            slot["last_used"] = _utcnow()
        return out


def try_start_or_reuse_session(
    key: str,
    create_driver_fn: Callable[[], Any],
) -> tuple[Any, bool, bool]:
    """
    Under session lock: if driver alive, return (driver, reused=True, replaced=False).
    Otherwise create via create_driver_fn(), store, return (driver, reused=False, replaced=...).
    """
    slot = _ensure_slot(key)
    with slot["lock"]:
        old = slot.get("driver")
        if old and _driver_alive(old):
            slot["last_used"] = _utcnow()
            return old, True, False
        if old:
            try:
                old.quit()
            except Exception:
                pass
            slot["driver"] = None
        driver = create_driver_fn()
        now = _utcnow()
        slot["driver"] = driver
        slot["created_at"] = now
        slot["last_used"] = now
        return driver, False, old is not None


def set_session_driver(key: str, driver: Any) -> None:
    """Store driver (caller already quit any previous)."""
    slot = _ensure_slot(key)
    with slot["lock"]:
        prev = slot.get("driver")
        if prev and prev is not driver:
            try:
                prev.quit()
            except Exception:
                pass
        now = _utcnow()
        slot["driver"] = driver
        if slot.get("created_at") is None:
            slot["created_at"] = now
        slot["last_used"] = now


def stop_session(key: str) -> bool:
    """Quit driver and remove slot. Returns True if a session existed."""
    with _registry_lock:
        slot = _slots.pop(key, None)
    if not slot:
        return False
    with slot["lock"]:
        driver = slot.get("driver")
        slot["driver"] = None
    if driver:
        try:
            driver.quit()
        except Exception:
            pass
    return True


def evict_dead_session(key: str) -> None:
    """If driver is dead, clear slot entry (no quit)."""
    with _registry_lock:
        slot = _slots.get(key)
    if not slot:
        return
    with slot["lock"]:
        d = slot.get("driver")
        if d and not _driver_alive(d):
            try:
                d.quit()
            except Exception:
                pass
            slot["driver"] = None


# Test hook
def _reset_for_tests() -> None:
    with _registry_lock:
        keys = list(_slots.keys())
    for k in keys:
        stop_session(k)
