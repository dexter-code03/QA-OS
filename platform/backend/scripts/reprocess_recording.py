#!/usr/bin/env python3
"""
Re-encode a stored run video using the same pipeline as the engine (QuickTime-friendly H.264 + yuv420p).

Usage (from platform/backend):

  uv run python scripts/reprocess_recording.py --project-id 1 --run-id 181
  uv run python scripts/reprocess_recording.py --path /path/to/run.mp4
  uv run python scripts/reprocess_recording.py --project-id 1 --run-id 42 --name run.mov   # .mov → run.mp4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _ensure_import_path() -> None:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def main() -> int:
    _ensure_import_path()

    from app.settings import settings
    from app.runner.video_compat import (
        ffmpeg_available,
        postprocess_mp4_for_broad_playback,
        transcode_mov_to_mp4,
    )

    p = argparse.ArgumentParser(description="Re-encode a run video artifact for QuickTime-friendly playback.")
    p.add_argument("--path", type=Path, help="Path to an existing .mp4 or .mov file")
    p.add_argument("--project-id", type=int, help="Project id (requires --run-id)")
    p.add_argument("--run-id", type=int, help="Run id (requires --project-id)")
    p.add_argument(
        "--name",
        default="run.mp4",
        help="Artifact filename when using project/run (default: run.mp4)",
    )
    args = p.parse_args()

    if args.path is not None:
        if args.project_id is not None or args.run_id is not None:
            p.error("Use either --path or --project-id/--run-id, not both")
        path = args.path.expanduser().resolve()
    elif args.project_id is not None and args.run_id is not None:
        path = settings.artifacts_dir / str(args.project_id) / str(args.run_id) / args.name
    else:
        p.error("Provide --path or both --project-id and --run-id")

    if not path.exists() or path.stat().st_size == 0:
        print(f"Missing or empty file: {path}", file=sys.stderr)
        return 1

    if not ffmpeg_available():
        print("No ffmpeg (system PATH or imageio-ffmpeg). Run: uv sync", file=sys.stderr)
        return 1

    suf = path.suffix.lower()
    if suf == ".mov":
        out_mp4 = path.parent / "run.mp4"
        print(f"Transcoding {path} → {out_mp4} …")
        if not transcode_mov_to_mp4(path, out_mp4):
            print("Transcode failed.", file=sys.stderr)
            return 1
        print("Done (simulator .mov → run.mp4).")
        return 0

    if suf in (".mp4", ".m4v"):
        print(f"Re-encoding {path} …")
        if not postprocess_mp4_for_broad_playback(path):
            print("Re-encode failed.", file=sys.stderr)
            return 1
        print("Done.")
        return 0

    print(f"Unsupported extension {path.suffix!r}; use .mp4, .m4v, or .mov", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
