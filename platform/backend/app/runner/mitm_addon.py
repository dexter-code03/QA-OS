"""mitmproxy addon — loaded by ``mitmdump -s mitm_addon.py``.

Runs in mitmproxy's own Python (3.10+), NOT in the project venv.
Prints one JSON line per completed HTTP flow to stdout.  The parent
process (engine.py) reads these lines from the subprocess pipe.
"""

import json
import sys
import time
from datetime import datetime, timezone

_MAX_REQ_BODY = 10_240   # 10 KB
_MAX_RES_BODY = 51_200   # 50 KB
_timings: dict[str, float] = {}


def _safe_body(content: bytes | None, limit: int) -> str | None:
    if not content:
        return None
    if len(content) > limit:
        try:
            return content[:limit].decode("utf-8", errors="replace") + f"… [truncated, {len(content)} bytes total]"
        except Exception:
            return f"<binary {len(content)} bytes>"
    try:
        return content.decode("utf-8")
    except Exception:
        return f"<binary {len(content)} bytes>"


def request(flow):
    _timings[flow.id] = time.time()


def response(flow):
    if "mitm.it" in flow.request.pretty_host:
        return

    start = _timings.pop(flow.id, time.time())
    duration_ms = round((time.time() - start) * 1000)

    entry = {
        "id": flow.id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": flow.request.method,
        "url": flow.request.pretty_url,
        "status_code": flow.response.status_code,
        "duration_ms": duration_ms,
        "req_headers": dict(flow.request.headers),
        "req_body": _safe_body(flow.request.content, _MAX_REQ_BODY),
        "res_headers": dict(flow.response.headers),
        "res_body": _safe_body(flow.response.content, _MAX_RES_BODY),
    }

    sys.stdout.write(json.dumps(entry, default=str) + "\n")
    sys.stdout.flush()
