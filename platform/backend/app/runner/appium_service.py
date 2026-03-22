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


def ensure_appium_running(
    timeout_s: int = 60,
    host: str | None = None,
    port: int | None = None,
) -> Optional[AppiumHandle]:
    """
    If Appium is already running on APPIUM_HOST:APPIUM_PORT, do nothing.
    Otherwise attempt to spawn `appium server` (Appium 2+; requires CLI on PATH
    for the same process that runs the backend).
    """
    host = host or settings.appium_host
    port = port or settings.appium_port
    if _is_port_open(host, port):
        return None

    # Never use PIPE here without draining: Appium logs can fill the buffer and
    # block startup before the HTTP port opens.
    try:
        proc = subprocess.Popen(
            ["appium", "server", "--address", host, "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return None

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
