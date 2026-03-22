"""Normalize run videos so they open in QuickTime, Windows Media Player, and browsers."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _ffmpeg_exe() -> str | None:
    """System PATH first, then bundled binary from imageio-ffmpeg (no Homebrew required)."""
    w = shutil.which("ffmpeg")
    if w:
        return w
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ffmpeg_available() -> bool:
    return _ffmpeg_exe() is not None


def _libx264_qt_friendly_tail() -> list[str]:
    """
    Full re-encode (no stream copy) so output is H.264 4:2:0 in a QuickTime-friendly MP4.
    Avoid Baseline+level 3.1 — that pair breaks on many 1080p+ phone screenrecords.
    """
    return [
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
    ]


def transcode_mov_to_mp4(mov_path: Path, mp4_path: Path) -> bool:
    """simctl .mov → H.264 MP4 (yuv420p, faststart)."""
    if not mov_path.exists() or mov_path.stat().st_size == 0:
        return False
    exe = _ffmpeg_exe()
    if not exe:
        return False
    mp4_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [exe, "-y", "-i", str(mov_path), *_libx264_qt_friendly_tail(), str(mp4_path)],
            check=True,
            capture_output=True,
            timeout=600,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return mp4_path.exists() and mp4_path.stat().st_size > 0


def postprocess_mp4_for_broad_playback(mp4_path: Path) -> bool:
    """
    After Android adb pull: always re-encode to H.264 + yuv420p + faststart.
    Stream-copy remux alone often still fails in QuickTime (device profile / pixel format).
    """
    if not mp4_path.exists() or mp4_path.stat().st_size == 0:
        return False
    exe = _ffmpeg_exe()
    if not exe:
        return False
    tmp = mp4_path.with_suffix(".mp4.qaos.tmp")
    try:
        subprocess.run(
            [exe, "-y", "-i", str(mp4_path), *_libx264_qt_friendly_tail(), str(tmp)],
            check=True,
            capture_output=True,
            timeout=600,
        )
        if tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(mp4_path)
            return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return False
