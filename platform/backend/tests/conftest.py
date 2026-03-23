"""Pytest fixtures: in-memory DB, FastAPI TestClient with auth + patched SessionLocal."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.helpers import get_auth_token
from app.main import app
from app.models import Base

# Routers (and runner) bind SessionLocal at import time; patch each so API uses the test engine.
_SESSION_LOCAL_MODULES = (
    "app.db",
    "app.runner.engine",
    "app.routers.ai",
    "app.routers.artifacts",
    "app.routers.batch_runs",
    "app.routers.builds",
    "app.routers.imports",
    "app.routers.integrations",
    "app.routers.modules",
    "app.routers.projects",
    "app.routers.reports",
    "app.routers.runs",
    "app.routers.screens",
    "app.routers.tests",
)


def _make_test_engine():
    """In-memory SQLite shared across threads (FastAPI runs sync routes in a thread pool)."""
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def db():
    engine = _make_test_engine()

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    TestSession = sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
    )
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def client(monkeypatch):
    """TestClient that authenticates automatically; HTTP routes use the in-memory DB."""
    engine = _make_test_engine()

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    TestSession = sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
    )
    for mod in _SESSION_LOCAL_MODULES:
        monkeypatch.setattr(f"{mod}.SessionLocal", TestSession)

    token = get_auth_token()
    with TestClient(app) as c:
        c.cookies.set("qa_os_token", token)
        yield c

    Base.metadata.drop_all(engine)
