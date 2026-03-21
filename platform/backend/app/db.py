from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .settings import settings, ensure_dirs


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    builds: Mapped[list["Build"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    modules: Mapped[list["Module"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    tests: Mapped[list["TestDefinition"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    runs: Mapped[list["Run"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Module(Base):
    __tablename__ = "modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="modules")
    suites: Mapped[list["TestSuite"]] = relationship(back_populates="module", cascade="all, delete-orphan")


class TestSuite(Base):
    __tablename__ = "test_suites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    module: Mapped["Module"] = relationship(back_populates="suites")
    tests: Mapped[list["TestDefinition"]] = relationship(back_populates="suite")


class Build(Base):
    __tablename__ = "builds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(50))
    file_name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    build_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    project: Mapped["Project"] = relationship(back_populates="builds")
    runs: Mapped[list["Run"]] = relationship(back_populates="build")


class TestDefinition(Base):
    __tablename__ = "tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    suite_id: Mapped[Optional[int]] = mapped_column(ForeignKey("test_suites.id", ondelete="SET NULL"), nullable=True)
    prerequisite_test_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tests.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    steps: Mapped[list[dict]] = mapped_column(JSON)  # legacy; kept in sync with platform_steps["android"]
    platform_steps: Mapped[dict] = mapped_column(JSON, default=dict)  # {"android": [...], "ios_sim": [...]}
    acceptance_criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Source of truth: what this test must validate
    fix_history: Mapped[list[dict]] = mapped_column(JSON, default=list)  # [{analysis, fixed_steps, changes, run_id?, created_at}]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="tests")
    suite: Mapped[Optional["TestSuite"]] = relationship(back_populates="tests")
    runs: Mapped[list["Run"]] = relationship(back_populates="test")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    build_id: Mapped[int] = mapped_column(ForeignKey("builds.id", ondelete="SET NULL"), nullable=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="SET NULL"), nullable=True)

    status: Mapped[str] = mapped_column(String(30), default="queued")  # queued|running|passed|failed|cancelled|error
    device_target: Mapped[str] = mapped_column(String(200), default="")  # udid/simulator name
    platform: Mapped[str] = mapped_column(String(50), default="")  # android|ios_sim
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    failure_category: Mapped[str] = mapped_column(String(50), default="")  # selector_not_found|element_timeout|assertion_failure|network_error|app_crash|other
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)  # {video:..., screenshots:[...], logs:[...]}

    project: Mapped["Project"] = relationship(back_populates="runs")
    build: Mapped[Optional["Build"]] = relationship(back_populates="runs")
    test: Mapped[Optional["TestDefinition"]] = relationship(back_populates="runs")


class ScreenFolder(Base):
    __tablename__ = "screen_folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = relationship()
    screens: Mapped[list["ScreenLibrary"]] = relationship(back_populates="folder", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_screen_folder_name"),
    )


class ScreenLibrary(Base):
    __tablename__ = "screen_library"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    build_id: Mapped[Optional[int]] = mapped_column(ForeignKey("builds.id", ondelete="SET NULL"), nullable=True)
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("screen_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    xml_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    screenshot_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    captured_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    auto_captured: Mapped[bool] = mapped_column(Integer, default=0)

    project: Mapped["Project"] = relationship()
    build: Mapped[Optional["Build"]] = relationship()
    folder: Mapped[Optional["ScreenFolder"]] = relationship(back_populates="screens")

    __table_args__ = (
        UniqueConstraint("project_id", "build_id", "name", "platform", name="uq_screen_per_build"),
    )


def _db_url() -> str:
    ensure_dirs()
    return f"sqlite+pysqlite:///{settings.db_path}"


engine = create_engine(_db_url(), connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    import sqlite3
    con = sqlite3.connect(str(settings.db_path))
    cur = con.cursor()
    cols = [row[1] for row in cur.execute("PRAGMA table_info(tests)").fetchall()]
    if "suite_id" not in cols:
        cur.execute("ALTER TABLE tests ADD COLUMN suite_id INTEGER REFERENCES test_suites(id) ON DELETE SET NULL")
        con.commit()
    if "prerequisite_test_id" not in cols:
        cur.execute("ALTER TABLE tests ADD COLUMN prerequisite_test_id INTEGER REFERENCES tests(id) ON DELETE SET NULL")
        con.commit()
    if "fix_history" not in cols:
        cur.execute("ALTER TABLE tests ADD COLUMN fix_history JSON DEFAULT '[]'")
        con.commit()
    if "acceptance_criteria" not in cols:
        cur.execute("ALTER TABLE tests ADD COLUMN acceptance_criteria TEXT")
        con.commit()
    if "platform_steps" not in cols:
        cur.execute("ALTER TABLE tests ADD COLUMN platform_steps JSON DEFAULT '{}'")
        con.commit()
        import json as _json

        rows = cur.execute("SELECT id, steps FROM tests WHERE steps IS NOT NULL").fetchall()
        for test_id, steps_raw in rows:
            try:
                if isinstance(steps_raw, str):
                    steps = _json.loads(steps_raw)
                else:
                    steps = steps_raw
                if not steps:
                    continue
                platform_steps = _json.dumps({"android": steps, "ios_sim": []})
                cur.execute("UPDATE tests SET platform_steps = ? WHERE id = ?", (platform_steps, test_id))
            except Exception:
                pass
        con.commit()
    # -- screen_library: folder_id --
    sl_cols = [row[1] for row in cur.execute("PRAGMA table_info(screen_library)").fetchall()]
    if sl_cols and "folder_id" not in sl_cols:
        cur.execute("ALTER TABLE screen_library ADD COLUMN folder_id INTEGER REFERENCES screen_folders(id) ON DELETE SET NULL")
        con.commit()

    # -- runs: failure_category --
    run_cols = [row[1] for row in cur.execute("PRAGMA table_info(runs)").fetchall()]
    if "failure_category" not in run_cols:
        cur.execute("ALTER TABLE runs ADD COLUMN failure_category VARCHAR(50) DEFAULT ''")
        con.commit()
        _backfill_failure_categories(cur)
        con.commit()
    con.close()


def _classify_error(msg: str) -> str:
    """Pattern-match an error message to a failure category."""
    if not msg:
        return "other"
    low = msg.lower()
    if any(k in low for k in ("nosuchelement", "no such element", "element not found", "selector")):
        return "selector_not_found"
    if any(k in low for k in ("timeout", "timed out", "waited")):
        return "element_timeout"
    if any(k in low for k in ("assert", "expected", "mismatch")):
        return "assertion_failure"
    if any(k in low for k in ("connectionerror", "httperror", "5xx", "502", "503", "504", "network")):
        return "network_error"
    if any(k in low for k in ("fatal", "anr", "crash", "nullpointer", "segfault")):
        return "app_crash"
    return "other"


def _backfill_failure_categories(cur) -> None:
    """One-time: classify existing failed/error runs."""
    rows = cur.execute(
        "SELECT id, error_message FROM runs WHERE status IN ('failed','error') AND (failure_category IS NULL OR failure_category = '')"
    ).fetchall()
    for run_id, err in rows:
        cat = _classify_error(err or "")
        cur.execute("UPDATE runs SET failure_category = ? WHERE id = ?", (cat, run_id))

