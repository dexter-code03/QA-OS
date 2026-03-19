from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from ..settings import settings


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


@dataclass
class AppiumHandle:
    process: subprocess.Popen
    host: str
    port: int


def ensure_appium_running(timeout_s: int = 15) -> Optional[AppiumHandle]:
    """
    If Appium is already running on APPIUM_HOST:APPIUM_PORT, do nothing.
    Otherwise attempt to spawn `appium` as a subprocess (requires global install).
    """
    host, port = settings.appium_host, settings.appium_port
    if _is_port_open(host, port):
        return None

    proc = subprocess.Popen(
        ["appium", "--address", host, "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    start = time.time()
    while time.time() - start < timeout_s:
        if _is_port_open(host, port):
            return AppiumHandle(process=proc, host=host, port=port)
        time.sleep(0.25)

    try:
        proc.terminate()
    except Exception:
        pass
    return None

