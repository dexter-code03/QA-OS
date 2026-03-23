"""FastAPI application — init, middleware, router registration."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .db import init_db
from .helpers import ALLOWED_ORIGINS, extract_request_token, get_auth_token
from .runner.engine import run_engine
from .settings import ensure_dirs

from .routers import (
    ai,
    artifacts,
    auth,
    batch_runs,
    builds,
    execution,
    imports,
    integrations,
    modules,
    projects,
    reports,
    runs,
    screens,
    tests,
)

app = FastAPI(title="QA Platform (Local Appium TestOps)", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    ensure_dirs()
    init_db()
    get_auth_token()
    run_engine.start()


@app.middleware("http")
async def require_local_token(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in {"/api/health", "/api/auth/token"}:
        return await call_next(request)

    if extract_request_token(request) != get_auth_token():
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)


# ── Register routers ──────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(integrations.router)
app.include_router(projects.router)
app.include_router(modules.router)
app.include_router(builds.router)
app.include_router(tests.router)
app.include_router(runs.router)
app.include_router(batch_runs.router)
app.include_router(screens.router)
app.include_router(ai.router)
app.include_router(imports.router)
app.include_router(reports.router)
app.include_router(artifacts.router)
app.include_router(execution.router)
