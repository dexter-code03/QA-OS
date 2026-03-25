"""Capture API traffic by reading Pulse's data store on iOS simulators.

Pulse (https://github.com/kean/Pulse) is an in-app network logger for iOS.
It stores HTTP transactions in a SQLite-backed CoreData store inside the
app's container directory on the simulator filesystem:

    ~/Library/Developer/CoreSimulator/Devices/<UDID>/data/Containers/
      Data/Application/<APP-UUID>/Library/Pulse/logs.sqlite

This module locates that store and reads transactions — no proxy, no certs,
no network interference.  Only works for iOS simulators (not physical devices).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_SIM_DEVICES_DIR = Path.home() / "Library" / "Developer" / "CoreSimulator" / "Devices"

# Pulse stores its data under different paths depending on version.
_PULSE_DB_CANDIDATES = [
    "Library/Pulse/logs.sqlite",
    "Library/Pulse/Logs.sqlite",
    "Library/Caches/Pulse/logs.sqlite",
    "Documents/Pulse/logs.sqlite",
]


@dataclass
class PulseHandle:
    poller_thread: threading.Thread
    db_path: Optional[Path] = None
    _stopped: bool = field(default=False, repr=False)


def _get_simulator_udid(device_target: str) -> Optional[str]:
    """Resolve a device target (name or UDID) to a booted simulator UDID."""
    try:
        r = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(r.stdout)
        for runtime, devices in data.get("devices", {}).items():
            for d in devices:
                if d.get("state") == "Booted":
                    if device_target in (d.get("udid", ""), d.get("name", "")):
                        return d["udid"]
                    if not device_target:
                        return d["udid"]
    except Exception:
        log.exception("Failed to list simulators")
    return None


def _find_app_container(sim_udid: str, bundle_id: str) -> Optional[Path]:
    """Find the app's data container on the simulator filesystem."""
    try:
        r = subprocess.run(
            ["xcrun", "simctl", "get_app_container", sim_udid, bundle_id, "data"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except Exception:
        pass
    return None


def _find_pulse_db(container: Path) -> Optional[Path]:
    """Locate the Pulse SQLite database inside the app container."""
    for candidate in _PULSE_DB_CANDIDATES:
        p = container / candidate
        if p.exists():
            return p
    # Fallback: search for any .sqlite file in a Pulse-like directory
    for dirpath, dirnames, filenames in os.walk(str(container / "Library")):
        if "pulse" in dirpath.lower():
            for f in filenames:
                if f.endswith(".sqlite"):
                    return Path(dirpath) / f
    return None


def _read_pulse_transactions(db_path: Path, after_id: int) -> list[dict]:
    """Read network transactions from the Pulse SQLite store."""
    rows = []
    try:
        conn = sqlite3.connect(str(db_path), timeout=3)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Pulse uses different table names across versions; try common ones
        for table in [
            "ZNETWORKTRANSACTIONENTITY",
            "ZNETWORKREQUESTENTITY",
            "ZPULSELOGMESSAGE",
            "ZNETWORKTASKENTITY",
        ]:
            try:
                cur.execute(f"SELECT * FROM {table} WHERE Z_PK > ? ORDER BY Z_PK", (after_id,))
                for row in cur.fetchall():
                    rows.append(dict(row))
                if rows:
                    break
            except sqlite3.OperationalError:
                continue
        conn.close()
    except Exception:
        log.exception("Failed to read Pulse DB at %s", db_path)
    return rows


def _row_to_entry(row: dict) -> dict[str, Any]:
    """Convert a Pulse DB row into our standard ApiLog dict."""
    pk = row.get("Z_PK", 0)

    url = row.get("ZURL") or row.get("ZREQUESTURL") or row.get("ZURLSTRING") or ""
    method = row.get("ZHTTPMETHOD") or row.get("ZMETHOD") or row.get("ZREQUESTMETHOD") or "GET"
    status = row.get("ZSTATUSCODE") or row.get("ZRESPONSECODE") or row.get("ZRESPONSESTATUS") or 0
    duration = row.get("ZDURATION") or row.get("ZELAPSEDTIME") or 0
    if isinstance(duration, float) and duration < 1000:
        duration = int(duration * 1000)

    req_body = row.get("ZREQUESTBODY") or row.get("ZREQUESTBODYDATA") or None
    res_body = row.get("ZRESPONSEBODY") or row.get("ZRESPONSEBODYDATA") or None
    if isinstance(req_body, bytes):
        req_body = req_body.decode("utf-8", errors="replace")
    if isinstance(res_body, bytes):
        res_body = res_body.decode("utf-8", errors="replace")

    ts_raw = row.get("ZTIMESTAMP") or row.get("ZCREATEDDATE") or row.get("ZSTARTDATE")
    if ts_raw and isinstance(ts_raw, (int, float)):
        # CoreData timestamps are seconds since 2001-01-01
        epoch = ts_raw + 978307200
        ts = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    else:
        ts = datetime.now(timezone.utc).isoformat()

    return {
        "id": str(pk),
        "timestamp": ts,
        "method": method.upper(),
        "url": url,
        "status_code": int(status) if status else 0,
        "duration_ms": int(duration),
        "req_headers": {},
        "req_body": req_body,
        "res_headers": {},
        "res_body": res_body,
        "source": "pulse",
    }


def is_pulse_available(device_target: str, bundle_id: str) -> bool:
    """Check if Pulse data store exists for the given app on a simulator."""
    udid = _get_simulator_udid(device_target)
    if not udid:
        return False
    container = _find_app_container(udid, bundle_id)
    if not container:
        return False
    return _find_pulse_db(container) is not None


def start_pulse_capture(
    device_target: str,
    bundle_id: str,
    on_log: Callable[[dict[str, Any]], None],
    poll_interval: float = 2.0,
) -> Optional[PulseHandle]:
    """Poll the Pulse DB for new transactions and emit them via on_log."""
    udid = _get_simulator_udid(device_target)
    if not udid:
        log.warning("No booted simulator found for %s", device_target)
        return None

    container = _find_app_container(udid, bundle_id)
    if not container:
        log.warning("App container not found for %s on %s", bundle_id, udid)
        return None

    db_path = _find_pulse_db(container)
    if not db_path:
        log.warning("Pulse DB not found in %s", container)
        return None

    handle = PulseHandle(poller_thread=threading.Thread(target=lambda: None), db_path=db_path)

    def _poller() -> None:
        last_pk = 0
        while not handle._stopped:
            time.sleep(poll_interval)
            if handle._stopped:
                break
            rows = _read_pulse_transactions(db_path, last_pk)
            for row in rows:
                entry = _row_to_entry(row)
                pk = row.get("Z_PK", 0)
                if pk > last_pk:
                    last_pk = pk
                try:
                    on_log(entry)
                except Exception:
                    log.exception("on_log callback failed")

    t = threading.Thread(target=_poller, daemon=True, name="pulse-poller")
    t.start()
    handle.poller_thread = t
    log.info("Pulse capture started for %s (db=%s)", bundle_id, db_path)
    return handle


def stop_pulse_capture(handle: Optional[PulseHandle]) -> None:
    """Stop polling the Pulse DB."""
    if handle is None or handle._stopped:
        return
    handle._stopped = True
    handle.poller_thread.join(timeout=5)
    log.info("Pulse capture stopped")
