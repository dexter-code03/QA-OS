"""Capture API traffic by parsing OkHttp / Retrofit log lines from adb logcat.

Works on any Android device or emulator — no root, no proxy, no certificates.
Requires the app to have OkHttp's HttpLoggingInterceptor enabled (standard in
debug builds).

Produces the same dict shape as proxy.py so the rest of the pipeline
(engine.py → WebSocket → frontend) works unchanged.
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# Common logcat tags emitted by OkHttp / Retrofit / Volley logging interceptors.
_KNOWN_TAGS = ("OkHttp", "Retrofit", "HttpClient", "API", "Network", "Volley")
_TAG_FILTER = " ".join(f"{t}:V" for t in _KNOWN_TAGS) + " *:S"

# Regex patterns for OkHttp HttpLoggingInterceptor output
_RE_REQ_LINE = re.compile(r"^--> (GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (.+)$")
_RE_REQ_END = re.compile(r"^--> END (GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)")
_RE_RES_LINE = re.compile(r"^<-- (\d{3})\s+\S*\s*(https?://\S+)\s*\((\d+)ms\)")
_RE_RES_END = re.compile(r"^<-- END HTTP")
_RE_HEADER = re.compile(r"^([A-Za-z][\w-]*):\s*(.+)$")


@dataclass
class LogcatHandle:
    process: subprocess.Popen
    reader_thread: threading.Thread
    _stopped: bool = field(default=False, repr=False)


@dataclass
class _PendingRequest:
    method: str = ""
    url: str = ""
    headers: dict = field(default_factory=dict)
    body_lines: list = field(default_factory=list)
    started_at: float = 0.0


def start_logcat_capture(
    device_serial: str,
    on_log: Callable[[dict[str, Any]], None],
) -> Optional[LogcatHandle]:
    """Start ``adb logcat`` and parse OkHttp log lines into API log entries."""
    # Clear logcat buffer first so we don't replay old entries
    try:
        subprocess.run(
            ["adb", "-s", device_serial, "logcat", "-c"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass

    cmd = ["adb", "-s", device_serial, "logcat", "-v", "tag", _TAG_FILTER]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
    except Exception:
        log.exception("Failed to start adb logcat")
        return None

    time.sleep(0.3)
    if proc.poll() is not None:
        log.error("adb logcat exited immediately")
        return None

    handle = LogcatHandle(process=proc, reader_thread=threading.Thread(target=lambda: None))

    def _reader() -> None:
        assert proc.stdout is not None
        pending: Optional[_PendingRequest] = None
        # Accumulate response state
        res_status: Optional[int] = None
        res_url: Optional[str] = None
        res_duration: int = 0
        res_headers: dict = {}
        res_body_lines: list = []
        in_response = False

        for raw_line in proc.stdout:
            if handle._stopped:
                break

            # Strip logcat tag prefix (e.g. "D/OkHttp  : ") to get the payload
            payload = raw_line.strip()
            colon_idx = payload.find(": ")
            if colon_idx != -1:
                payload = payload[colon_idx + 2:]
            else:
                continue

            # ── Request start ────────────────────────────────
            m = _RE_REQ_LINE.match(payload)
            if m:
                pending = _PendingRequest(
                    method=m.group(1),
                    url=m.group(2),
                    started_at=time.time(),
                )
                in_response = False
                continue

            # ── Request end ──────────────────────────────────
            if _RE_REQ_END.match(payload):
                continue

            # ── Response start ───────────────────────────────
            m = _RE_RES_LINE.match(payload)
            if m:
                res_status = int(m.group(1))
                res_url = m.group(2)
                res_duration = int(m.group(3))
                res_headers = {}
                res_body_lines = []
                in_response = True
                continue

            # ── Response end → emit log entry ────────────────
            if _RE_RES_END.match(payload):
                if res_status is not None:
                    req_url = (pending.url if pending else res_url) or res_url or ""
                    req_method = (pending.method if pending else "GET")
                    entry: dict[str, Any] = {
                        "id": uuid.uuid4().hex[:16],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "method": req_method,
                        "url": req_url,
                        "status_code": res_status,
                        "duration_ms": res_duration,
                        "req_headers": pending.headers if pending else {},
                        "req_body": "\n".join(pending.body_lines) if pending and pending.body_lines else None,
                        "res_headers": res_headers,
                        "res_body": "\n".join(res_body_lines) if res_body_lines else None,
                        "source": "logcat",
                    }
                    try:
                        on_log(entry)
                    except Exception:
                        log.exception("on_log callback failed")
                pending = None
                res_status = None
                in_response = False
                continue

            # ── Header or body line ──────────────────────────
            hm = _RE_HEADER.match(payload)
            if hm:
                if in_response:
                    res_headers[hm.group(1)] = hm.group(2)
                elif pending:
                    pending.headers[hm.group(1)] = hm.group(2)
                continue

            # Anything else while inside a request/response block is a body line
            if in_response:
                res_body_lines.append(payload)
            elif pending:
                pending.body_lines.append(payload)

    t = threading.Thread(target=_reader, daemon=True, name="logcat-api-reader")
    t.start()
    handle.reader_thread = t
    log.info("logcat API capture started for device %s", device_serial)
    return handle


def stop_logcat_capture(handle: Optional[LogcatHandle]) -> None:
    """Stop the logcat capture subprocess."""
    if handle is None or handle._stopped:
        return
    handle._stopped = True
    proc = handle.process
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            log.exception("Error stopping logcat capture")
    handle.reader_thread.join(timeout=2)
    log.info("logcat API capture stopped")
