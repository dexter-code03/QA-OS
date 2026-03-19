# QA OS

Mobile testing platform with Appium, AI step generation, and Katalon export.

## Run

```bash
# Backend
cd platform/backend && pip install -r requirements.txt && uvicorn app.main:app --host 127.0.0.1 --port 9001

# Frontend (separate terminal)
cd platform/frontend && npm install && npm run dev
```

Open http://localhost:5173
