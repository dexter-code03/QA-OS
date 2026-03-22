## QA Platform Backend (Local)

### Run

From repo root:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r platform/backend/requirements.txt
cd platform/backend
uvicorn app.main:app --reload --port 9001
```

Backend health: `GET http://127.0.0.1:9001/api/health`

### Notes

- SQLite DB is stored under `platform/backend/.data/` (gitignored).
- Uploads stored under `platform/backend/uploads/` (gitignored).
- Artifacts stored under `platform/backend/artifacts/` (gitignored).
- **Run video (MP4):** The backend depends on **`imageio-ffmpeg`**, which ships a portable **`ffmpeg`** binary (no Homebrew required). If **`ffmpeg`** is also on **`PATH`**, that binary is used first. Android pulls and iOS sim **`run.mov`** are **re-encoded** to **H.264 + yuv420p + faststart** (stream-copy is not enough for reliable QuickTime playback). If **`ffmpeg`** cannot be resolved, Android files stay as raw `adb` MP4; iOS stays **`.mov`**.
- **Fix an old recording on disk** (same encode as new runs): from `platform/backend`, run  
  `uv run python scripts/reprocess_recording.py --project-id <pid> --run-id <rid>`  
  or `uv run python scripts/reprocess_recording.py --path /path/to/run.mp4` (also supports **`run.mov`** → **`run.mp4`** next to it).

### Screen Library capture session (Appium)

- **Start build** opens one long-lived Appium session per `(project, platform, device, build)`; **Capture** only reads hierarchy + screenshot until you **Stop** or the session times out.
- Sessions live **in memory** in this process. After **`uvicorn --reload`**, a hot reload, or multiple workers, you must tap **Start build** again. Use a **single worker** if you rely on sticky sessions.
- **Manual QA (10+ captures):** Start build once → Capture 10+ times while navigating in-app → confirm the emulator stays in the app without reinstall loops → Stop → Start again → one capture.
