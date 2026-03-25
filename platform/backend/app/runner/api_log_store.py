"""Lightweight file-based storage for API traffic logs captured by mitmproxy."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List

log = logging.getLogger(__name__)

_FILENAME = "api_logs.json"


def save_api_logs(run_dir: Path, logs: List[Any]) -> None:
    """Persist captured API logs to the run's artifacts directory."""
    if not logs:
        return
    path = run_dir / _FILENAME
    try:
        path.write_text(json.dumps(logs, default=str))
    except Exception:
        log.exception("Failed to save API logs to %s", path)


def load_api_logs(run_dir: Path) -> List[Any]:
    """Read previously saved API logs, or return an empty list."""
    path = run_dir / _FILENAME
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        log.exception("Failed to load API logs from %s", path)
        return []
