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

