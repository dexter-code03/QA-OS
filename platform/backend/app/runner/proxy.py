"""Manage a mitmdump subprocess for capturing device HTTP traffic.

mitmdump is called as a CLI tool (not imported as a library) so the project
can stay on Python 3.9 while mitmproxy uses its own 3.10+ runtime.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_ADDON_PATH = str(Path(__file__).with_name("mitm_addon.py"))
_DEFAULT_PORT = 8081


@dataclass
class ProxyHandle:
    process: subprocess.Popen
    reader_thread: threading.Thread
    port: int = _DEFAULT_PORT
    _stopped: bool = field(default=False, repr=False)


def is_mitmdump_available() -> bool:
    """Return True if ``mitmdump`` is on PATH."""
    return shutil.which("mitmdump") is not None


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    """Block until *host:port* accepts TCP connections, or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def start_proxy(
    on_log: Callable[[dict[str, Any]], None],
    port: int = _DEFAULT_PORT,
) -> Optional[ProxyHandle]:
    """Spawn ``mitmdump`` and return a handle, or None if unavailable."""
    if not is_mitmdump_available():
        log.warning("mitmdump not found on PATH — API logging disabled for this run")
        return None

    cmd = [
        "mitmdump",
        "--listen-host", "127.0.0.1",
        "--listen-port", str(port),
        "-s", _ADDON_PATH,
        "--set", "flow_detail=0",
        "--ssl-insecure",
        "-q",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except Exception:
        log.exception("Failed to start mitmdump")
        return None

    # Give the process a moment to either die or start binding.
    time.sleep(1)
    if proc.poll() is not None:
        stderr = proc.stderr.read() if proc.stderr else ""
        log.error("mitmdump exited immediately (rc=%s): %s", proc.returncode, stderr[:500])
        return None

    if not _wait_for_port("127.0.0.1", port, timeout=8):
        log.error("mitmdump did not bind to :%d within timeout — killing", port)
        proc.kill()
        proc.wait(timeout=3)
        return None

    handle = ProxyHandle(process=proc, reader_thread=threading.Thread(target=lambda: None), port=port)

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if handle._stopped:
                break
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                on_log(entry)
            except json.JSONDecodeError:
                log.debug("Non-JSON line from mitmdump: %s", line[:200])

    t = threading.Thread(target=_reader, daemon=True, name="mitm-reader")
    t.start()
    handle.reader_thread = t
    log.info("mitmdump ready on :%d (pid=%d)", port, proc.pid)
    return handle


def stop_proxy(handle: Optional[ProxyHandle]) -> None:
    """Gracefully stop the mitmdump subprocess."""
    if handle is None or handle._stopped:
        return
    handle._stopped = True
    proc = handle.process
    if proc.poll() is None:
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            log.exception("Error stopping mitmdump")
    handle.reader_thread.join(timeout=2)
    log.info("mitmdump stopped")


_EMULATOR_HOST_LOOPBACK = "10.0.2.2"
_CERT_PEM = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"


def ensure_mitm_cert_installed(device_serial: str) -> bool:
    """Best-effort install of mitmproxy CA on the device.

    On rootable images (Google APIs / AOSP) this installs as a system CA.
    On non-rootable images (Google Play) it pushes the cert and opens the
    installer UI so the user can tap to confirm (one-time setup).
    Returns True if the cert is (or was already) in the system store.
    """
    if not _CERT_PEM.exists():
        log.warning("mitmproxy CA cert not found at %s — run mitmdump once to generate it", _CERT_PEM)
        return False

    try:
        import hashlib
        out = subprocess.run(
            ["openssl", "x509", "-inform", "PEM", "-subject_hash_old", "-noout", "-in", str(_CERT_PEM)],
            capture_output=True, text=True, timeout=5,
        )
        cert_hash = out.stdout.strip()
        if not cert_hash:
            log.warning("Could not compute cert hash")
            return False
    except Exception:
        log.exception("openssl not available for cert hash")
        return False

    cert_filename = f"{cert_hash}.0"

    # Check if already in system store
    check = subprocess.run(
        ["adb", "-s", device_serial, "shell", f"test -f /system/etc/security/cacerts/{cert_filename} && echo yes || "
         f"test -f /apex/com.android.conscrypt/cacerts/{cert_filename} && echo yes || echo no"],
        capture_output=True, text=True, timeout=5,
    )
    if "yes" in check.stdout:
        log.info("mitmproxy CA already in system trust store")
        return True

    # Try root-based installation
    root_result = subprocess.run(["adb", "-s", device_serial, "root"], capture_output=True, text=True, timeout=10)
    if "cannot run as root" not in root_result.stdout.lower():
        log.info("adb root succeeded — installing as system CA")
        time.sleep(2)
        # Reconnect after root
        subprocess.run(["adb", "-s", device_serial, "wait-for-device"], timeout=15)
        subprocess.run(["adb", "-s", device_serial, "remount"], capture_output=True, timeout=15)
        time.sleep(1)

        # Determine which cert directory exists
        apex_check = subprocess.run(
            ["adb", "-s", device_serial, "shell", "test -d /apex/com.android.conscrypt/cacerts && echo apex || echo system"],
            capture_output=True, text=True, timeout=5,
        )
        if "apex" in apex_check.stdout:
            # API 34+: use tmpfs overlay approach
            tmp_dir = "/data/local/tmp/cacerts"
            subprocess.run(["adb", "-s", device_serial, "shell", f"mkdir -p {tmp_dir}"], timeout=5)
            subprocess.run(["adb", "-s", device_serial, "shell", f"cp /apex/com.android.conscrypt/cacerts/* {tmp_dir}/"], timeout=10)
            subprocess.run(["adb", "-s", device_serial, "push", str(_CERT_PEM), f"{tmp_dir}/{cert_filename}"], timeout=5)
            subprocess.run(["adb", "-s", device_serial, "shell", f"chmod 644 {tmp_dir}/{cert_filename}"], timeout=5)
            mount_res = subprocess.run(
                ["adb", "-s", device_serial, "shell", f"mount -o bind {tmp_dir} /apex/com.android.conscrypt/cacerts"],
                capture_output=True, text=True, timeout=5,
            )
            if mount_res.returncode == 0:
                log.info("Mounted mitmproxy CA into APEX cert store")
                return True
            # Fallback: try direct push to /system/etc/security/cacerts
            cert_dir = "/system/etc/security/cacerts"
        else:
            cert_dir = "/system/etc/security/cacerts"

        subprocess.run(["adb", "-s", device_serial, "push", str(_CERT_PEM), f"{cert_dir}/{cert_filename}"], timeout=5)
        subprocess.run(["adb", "-s", device_serial, "shell", f"chmod 644 {cert_dir}/{cert_filename}"], timeout=5)
        # Un-root for normal operation
        subprocess.run(["adb", "-s", device_serial, "unroot"], capture_output=True, timeout=10)
        time.sleep(2)
        subprocess.run(["adb", "-s", device_serial, "wait-for-device"], timeout=15)
        log.info("Installed mitmproxy CA as system cert at %s/%s", cert_dir, cert_filename)
        return True

    # Non-rootable image — push cert and open installer UI for user
    log.warning("Cannot get root — pushing cert for manual user installation")
    subprocess.run(["adb", "-s", device_serial, "push", str(_CERT_PEM), "/sdcard/mitmproxy-ca-cert.cer"], timeout=5)
    subprocess.run(
        ["adb", "-s", device_serial, "shell", "am", "start", "-n",
         "com.android.certinstaller/.CertInstallerMain",
         "-a", "android.intent.action.VIEW",
         "-t", "application/x-x509-ca-cert",
         "-d", "file:///sdcard/mitmproxy-ca-cert.cer"],
        capture_output=True, timeout=10,
    )
    log.warning("Cert installer opened — user must confirm installation on device")
    return False


def configure_android_proxy(device_serial: str, host: str = _EMULATOR_HOST_LOOPBACK, port: int = _DEFAULT_PORT) -> bool:
    """Route emulator/device HTTP traffic through the proxy.

    Uses 10.0.2.2 by default — the Android emulator's special alias for the
    host machine's loopback.  mitmdump listens on 127.0.0.1 on the host, and
    10.0.2.2 inside the emulator maps to exactly that address.
    """
    try:
        subprocess.run(
            ["adb", "-s", device_serial, "shell", "settings", "put", "global", "http_proxy", f"{host}:{port}"],
            check=True, capture_output=True, timeout=10,
        )
        return True
    except Exception:
        log.exception("Failed to set Android proxy on %s", device_serial)
        return False


def clear_android_proxy(device_serial: str) -> bool:
    """Remove proxy setting from the device."""
    try:
        subprocess.run(
            ["adb", "-s", device_serial, "shell", "settings", "delete", "global", "http_proxy"],
            check=True, capture_output=True, timeout=10,
        )
        subprocess.run(
            ["adb", "-s", device_serial, "shell", "settings", "put", "global", "http_proxy", ":0"],
            capture_output=True, timeout=10,
        )
        return True
    except Exception:
        log.exception("Failed to clear Android proxy on %s", device_serial)
        return False


def _get_active_network_service() -> Optional[str]:
    """Return the active macOS network service name (e.g. 'Wi-Fi')."""
    try:
        out = subprocess.run(
            ["networksetup", "-listallnetworkservices"],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines():
            if line.startswith("*"):
                continue
            if "wi-fi" in line.lower() or "ethernet" in line.lower():
                return line.strip()
    except Exception:
        pass
    return "Wi-Fi"


def configure_ios_proxy(host: str = "127.0.0.1", port: int = _DEFAULT_PORT) -> bool:
    """Set macOS system HTTP/HTTPS proxy (iOS simulators share the host network)."""
    svc = _get_active_network_service()
    try:
        subprocess.run(["networksetup", "-setwebproxy", svc, host, str(port)], check=True, capture_output=True, timeout=10)
        subprocess.run(["networksetup", "-setsecurewebproxy", svc, host, str(port)], check=True, capture_output=True, timeout=10)
        return True
    except Exception:
        log.exception("Failed to set macOS system proxy for iOS simulator")
        return False


def clear_ios_proxy() -> bool:
    """Remove macOS system HTTP/HTTPS proxy."""
    svc = _get_active_network_service()
    try:
        subprocess.run(["networksetup", "-setwebproxystate", svc, "off"], check=True, capture_output=True, timeout=10)
        subprocess.run(["networksetup", "-setsecurewebproxystate", svc, "off"], check=True, capture_output=True, timeout=10)
        return True
    except Exception:
        log.exception("Failed to clear macOS system proxy")
        return False
