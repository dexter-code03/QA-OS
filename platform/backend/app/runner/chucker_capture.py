"""Capture API traffic by reading Chucker's SQLite database on Android devices.

Chucker (https://github.com/ChuckerTeam/chucker) is an in-app HTTP inspector
that many Android debug/pre-prod builds include.  It stores every OkHttp
transaction in a local SQLite DB at:

    /data/data/<package>/databases/chucker.db

This module supports three access strategies (tried in order):
  1. ``adb shell sqlite3`` — works on rootable emulators (Google APIs images)
  2. Appium ``driver.pull_file`` — works on debuggable apps
  3. ``adb exec-out cat`` via run-as — works on debuggable apps

Requires either a rootable emulator OR a debuggable app build.
"""
from __future__ import annotations

import base64
import json
import logging
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_CHUCKER_DB_PATH = "/data/data/%s/databases/chucker.db"
_TABLE_CANDIDATES = ("transactions", "http_transactions")

_SELECT_ALL_SQL = "SELECT * FROM %s WHERE id > %d ORDER BY id"

# Canonical name -> known DB column variants across Chucker 3.x / 4.x
_COL_ALIASES: dict[str, tuple[str, ...]] = {
    "id":              ("id",),
    "requestDate":     ("requestDate", "request_date"),
    "responseDate":    ("responseDate", "response_date"),
    "tookMs":          ("tookMs", "took_ms", "duration"),
    "method":          ("method",),
    "url":             ("url",),
    "responseCode":    ("responseCode", "response_code", "status_code", "statusCode"),
    "requestHeaders":  ("requestHeaders", "request_headers"),
    "requestBody":     ("requestBody", "request_body"),
    "responseHeaders": ("responseHeaders", "response_headers"),
    "responseBody":    ("responseBody", "response_body"),
    "error":           ("error",),
}

_REVERSE_ALIAS: dict[str, str] = {}
for _canon, _aliases in _COL_ALIASES.items():
    for _a in _aliases:
        _REVERSE_ALIAS[_a] = _canon
        _REVERSE_ALIAS[_a.lower()] = _canon

# Headers that only ever appear in HTTP responses, never in requests.
_RESPONSE_ONLY_HEADERS = frozenset({
    "strict-transport-security", "set-cookie", "www-authenticate",
    "proxy-authenticate", "age", "server", "vary", "x-powered-by",
    "access-control-allow-origin", "access-control-allow-methods",
    "access-control-allow-headers", "access-control-expose-headers",
    "access-control-max-age", "x-frame-options", "x-content-type-options",
    "x-xss-protection", "content-security-policy", "referrer-policy",
    "permissions-policy", "retry-after", "alt-svc", "nel", "report-to",
})


def _normalize_row(row: dict) -> dict:
    """Map column names from any Chucker DB schema variant to canonical names."""
    out: dict = {}
    for key, value in row.items():
        canon = _REVERSE_ALIAS.get(key) or _REVERSE_ALIAS.get(key.lower())
        out[canon or key] = value
    return out


@dataclass
class ChuckerHandle:
    poller_thread: threading.Thread
    driver: Any = field(default=None, repr=False)
    _stopped: bool = field(default=False, repr=False)
    _access_method: Optional[str] = field(default=None, repr=False)
    _table_name: str = field(default="transactions", repr=False)


def _detect_table(serial: str, package: str) -> str:
    """Probe the Chucker DB for the actual transactions table name."""
    db = _CHUCKER_DB_PATH % package
    for table in _TABLE_CANDIDATES:
        try:
            r = subprocess.run(
                ["adb", "-s", serial, "shell",
                 f"sqlite3 '{db}' \"SELECT count(*) FROM {table} LIMIT 1\""],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip().isdigit():
                return table
        except Exception:
            pass
    return "transactions"


# ── Strategy 1: adb shell sqlite3 (rootable devices) ───────────────

def _query_via_sqlite3(serial: str, package: str, table: str, after_id: int) -> Optional[list[dict]]:
    db = _CHUCKER_DB_PATH % package
    sql = _SELECT_ALL_SQL % (table, after_id)
    cmd = ["adb", "-s", serial, "shell", f"sqlite3 -json '{db}' \"{sql}\""]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip().startswith("["):
            return json.loads(r.stdout.strip())
    except Exception:
        pass
    return None


# ── Strategy 2: pull DB via Appium driver ───────────────────────────

def _pull_and_query_via_driver(driver: Any, package: str, table: str, after_id: int, tmp_dir: Path) -> Optional[list[dict]]:
    local_db = tmp_dir / "chucker.db"
    for path in [f"@{package}/databases/chucker.db", _CHUCKER_DB_PATH % package]:
        try:
            b64 = driver.pull_file(path)
            data = base64.b64decode(b64)
            if len(data) > 100:
                local_db.write_bytes(data)
                conn = sqlite3.connect(str(local_db), timeout=3)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    f"SELECT * FROM {table} WHERE id > ? ORDER BY id",
                    (after_id,),
                )
                rows = [dict(r) for r in cur.fetchall()]
                conn.close()
                local_db.unlink(missing_ok=True)
                return rows
        except Exception:
            pass
    local_db.unlink(missing_ok=True)
    return None


# ── Strategy 3: adb exec-out cat + local sqlite3 ───────────────────

def _pull_and_query_via_adb(serial: str, package: str, table: str, after_id: int, tmp_dir: Path) -> Optional[list[dict]]:
    local_db = tmp_dir / "chucker.db"
    db = _CHUCKER_DB_PATH % package
    for cmd in [
        ["adb", "-s", serial, "exec-out", "cat", db],
        ["adb", "-s", serial, "exec-out", "run-as", package, "cat", "databases/chucker.db"],
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=10)
            if r.returncode == 0 and len(r.stdout) > 100:
                local_db.write_bytes(r.stdout)
                conn = sqlite3.connect(str(local_db), timeout=3)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    f"SELECT * FROM {table} WHERE id > ? ORDER BY id",
                    (after_id,),
                )
                rows = [dict(r) for r in cur.fetchall()]
                conn.close()
                local_db.unlink(missing_ok=True)
                return rows
        except Exception:
            pass
    local_db.unlink(missing_ok=True)
    return None


# ── Shared helpers ──────────────────────────────────────────────────

def _parse_chucker_headers(
    raw: Optional[str],
    strip_response_only: bool = False,
) -> dict:
    """Parse headers stored by Chucker in any of its known formats.

    Supports:
      - JSON array:  [{"name":"K","value":"V"}, ...]   (Chucker 3.x)
      - JSON object: {"K":"V", ...}
      - Plain text:  "K: V\\nK2: V2"                   (Chucker 4.x)
    """
    if not raw:
        return {}
    raw = raw.strip()
    headers: dict[str, str] = {}

    if raw.startswith(("[", "{")):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("key") or ""
                        value = item.get("value") or item.get("val") or ""
                        if name:
                            headers[str(name)] = str(value)
            elif isinstance(parsed, dict):
                headers = {str(k): str(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    if not headers:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            sep = line.find(": ")
            if sep > 0:
                headers[line[:sep]] = line[sep + 2:]

    if strip_response_only and headers:
        headers = {
            k: v for k, v in headers.items()
            if k.lower() not in _RESPONSE_ONLY_HEADERS
        }

    return headers


def _row_to_entry(row: dict) -> dict[str, Any]:
    row = _normalize_row(row)
    req_date = row.get("requestDate")
    ts = (
        datetime.fromtimestamp(req_date / 1000, tz=timezone.utc).isoformat()
        if req_date
        else datetime.now(timezone.utc).isoformat()
    )
    return {
        "id": str(row.get("id", uuid.uuid4().hex[:16])),
        "timestamp": ts,
        "method": row.get("method", "GET"),
        "url": row.get("url", ""),
        "status_code": row.get("responseCode") or 0,
        "duration_ms": row.get("tookMs") or 0,
        "req_headers": _parse_chucker_headers(row.get("requestHeaders"), strip_response_only=True),
        "req_body": row.get("requestBody"),
        "res_headers": _parse_chucker_headers(row.get("responseHeaders")),
        "res_body": row.get("responseBody"),
        "error": row.get("error"),
        "source": "chucker",
    }


def is_chucker_available(device_serial: str, package: str) -> bool:
    """Check if Chucker DB exists. Returns True optimistically if we can't tell."""
    try:
        r = subprocess.run(
            ["adb", "-s", device_serial, "shell",
             f"test -f {_CHUCKER_DB_PATH % package} && echo yes"],
            capture_output=True, text=True, timeout=5,
        )
        if "yes" in r.stdout:
            return True
    except Exception:
        pass
    return True  # optimistic — let the poller discover failures


def clear_chucker_db(device_serial: str, package: str, table: str = "transactions") -> None:
    """Remove leftover Chucker data so the DB doesn't bloat across runs.

    Deletes the DB files entirely (Chucker recreates them on first write).
    Falls back to DELETE + VACUUM if rm fails.
    """
    db = _CHUCKER_DB_PATH % package
    try:
        subprocess.run(
            ["adb", "-s", device_serial, "shell",
             f"rm -f '{db}' '{db}-wal' '{db}-shm' '{db}-journal'"],
            capture_output=True, text=True, timeout=5,
        )
        return
    except Exception:
        pass
    try:
        subprocess.run(
            ["adb", "-s", device_serial, "shell",
             f"sqlite3 '{db}' \"DELETE FROM {table}; VACUUM;\""],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        pass


def start_chucker_capture(
    device_serial: str,
    package: str,
    on_log: Callable[[dict[str, Any]], None],
    poll_interval: float = 3.0,
) -> ChuckerHandle:
    """Start polling Chucker DB for new transactions."""
    table = _detect_table(device_serial, package)
    clear_chucker_db(device_serial, package, table)
    handle = ChuckerHandle(poller_thread=threading.Thread(target=lambda: None), _table_name=table)
    tmp_dir = Path(tempfile.mkdtemp(prefix="chucker_"))

    def _poller() -> None:
        last_id = 0

        # Let the app finish launching before touching the DB to avoid
        # SQLite lock contention that causes ANR / "not responding".
        _startup_wait = 6.0
        _waited = 0.0
        while _waited < _startup_wait and not handle._stopped:
            time.sleep(0.5)
            _waited += 0.5

        while not handle._stopped:
            time.sleep(poll_interval)
            if handle._stopped:
                break

            rows = None

            tbl = handle._table_name

            # Strategy 1: sqlite3 on device (rootable emulators)
            if handle._access_method in (None, "sqlite3"):
                rows = _query_via_sqlite3(device_serial, package, tbl, last_id)
                if rows is not None and handle._access_method is None:
                    handle._access_method = "sqlite3"
                    print(f"[CHUCKER] using sqlite3 on device (table={tbl})", flush=True)

            # Strategy 2: pull via Appium driver (debuggable apps)
            if rows is None and handle.driver is not None and handle._access_method in (None, "driver"):
                rows = _pull_and_query_via_driver(handle.driver, package, tbl, last_id, tmp_dir)
                if rows is not None and handle._access_method is None:
                    handle._access_method = "driver"
                    print(f"[CHUCKER] using Appium pull_file (table={tbl})", flush=True)

            # Strategy 3: adb cat + local sqlite3
            if rows is None and handle._access_method in (None, "adb_cat"):
                rows = _pull_and_query_via_adb(device_serial, package, tbl, last_id, tmp_dir)
                if rows is not None and handle._access_method is None:
                    handle._access_method = "adb_cat"
                    print(f"[CHUCKER] using adb exec-out cat (table={tbl})", flush=True)

            if not rows:
                continue

            for row in rows:
                entry = _row_to_entry(row)
                row_id = row.get("id", 0)
                if row_id > last_id:
                    last_id = row_id
                try:
                    on_log(entry)
                except Exception:
                    log.exception("on_log callback failed")

    t = threading.Thread(target=_poller, daemon=True, name="chucker-poller")
    t.start()
    handle.poller_thread = t
    print(f"[CHUCKER] capture started for {package} on {device_serial}", flush=True)
    return handle


def stop_chucker_capture(handle: Optional[ChuckerHandle]) -> None:
    if handle is None or handle._stopped:
        return
    handle._stopped = True
    handle.poller_thread.join(timeout=5)
    print(f"[CHUCKER] capture stopped (method={handle._access_method})", flush=True)
