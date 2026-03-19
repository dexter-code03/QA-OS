from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class IOSSimRecording:
    udid: str
    out_path: Path
    proc: subprocess.Popen


def _resolve_sim_udid(device_target: str) -> str:
    """
    Accepts either a UDID or a simulator name.
    If a name is provided, tries to find a BOOTED simulator with that name.
    """
    if not device_target:
        return "booted"

    if "-" in device_target and len(device_target) >= 20:
        return device_target

    try:
        out = subprocess.check_output(["xcrun", "simctl", "list", "devices", "-j"], text=True)
        data = json.loads(out)
        devices = data.get("devices", {})
        for runtime, sims in devices.items():
            for sim in sims:
                if sim.get("name") == device_target and sim.get("state") == "Booted":
                    return sim.get("udid") or "booted"
    except Exception:
        pass

    return "booted"


def start_recording(device_target: str, out_path: Path) -> Optional[IOSSimRecording]:
    udid = _resolve_sim_udid(device_target)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(
            ["xcrun", "simctl", "io", udid, "recordVideo", "--force", str(out_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)
        return IOSSimRecording(udid=udid, out_path=out_path, proc=proc)
    except Exception:
        return None


def stop(rec: IOSSimRecording) -> bool:
    try:
        rec.proc.terminate()
    except Exception:
        pass
    try:
        rec.proc.wait(timeout=6)
    except Exception:
        try:
            rec.proc.kill()
        except Exception:
            pass
    return rec.out_path.exists() and rec.out_path.stat().st_size > 0

