from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AndroidRecording:
    udid: str
    device_path: str
    proc: subprocess.Popen


def _adb(udid: str) -> list[str]:
    return ["adb", "-s", udid] if udid else ["adb"]


def start_screenrecord(udid: str, device_path: str = "/sdcard/qa_platform_run.mp4") -> Optional[AndroidRecording]:
    """
    Starts `adb shell screenrecord` on the device.
    Returns a handle if started successfully.
    """
    cmd = _adb(udid) + ["shell", "screenrecord", "--bit-rate", "4000000", device_path]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # give it a moment to start
        time.sleep(0.4)
        return AndroidRecording(udid=udid, device_path=device_path, proc=proc)
    except Exception:
        return None


def stop_and_pull(rec: AndroidRecording, out_path: Path) -> bool:
    try:
        rec.proc.terminate()
    except Exception:
        pass
    try:
        rec.proc.wait(timeout=5)
    except Exception:
        try:
            rec.proc.kill()
        except Exception:
            pass

    # pull video
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pull = _adb(rec.udid) + ["pull", rec.device_path, str(out_path)]
        subprocess.check_call(pull, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # best-effort cleanup
        rm = _adb(rec.udid) + ["shell", "rm", "-f", rec.device_path]
        subprocess.call(rm, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception:
        return False

