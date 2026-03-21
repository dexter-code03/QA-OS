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
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)  # {video:..., screenshots:[...], logs:[...]}

    project: Mapped["Project"] = relationship(back_populates="runs")
    build: Mapped[Optional["Build"]] = relationship(back_populates="runs")
    test: Mapped[Optional["TestDefinition"]] = relationship(back_populates="runs")


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
    con.close()

