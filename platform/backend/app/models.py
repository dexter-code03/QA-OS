from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, deferred, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    builds: Mapped[list["Build"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    modules: Mapped[list["Module"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    tests: Mapped[list["TestDefinition"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    runs: Mapped[list["Run"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Module(Base):
    __tablename__ = "modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="modules")
    suites: Mapped[list["TestSuite"]] = relationship(back_populates="module", cascade="all, delete-orphan")


class TestSuite(Base):
    __tablename__ = "test_suites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    module: Mapped["Module"] = relationship(back_populates="suites")
    tests: Mapped[list["TestDefinition"]] = relationship(back_populates="suite", passive_deletes=True)


class Build(Base):
    __tablename__ = "builds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(50))
    file_name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    build_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    project: Mapped["Project"] = relationship(back_populates="builds")
    runs: Mapped[list["Run"]] = relationship(back_populates="build")


class TestDefinition(Base):
    __tablename__ = "tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    suite_id: Mapped[Optional[int]] = mapped_column(ForeignKey("test_suites.id", ondelete="SET NULL"), nullable=True, index=True)
    prerequisite_test_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tests.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    steps: Mapped[list[dict]] = mapped_column(JSON)
    platform_steps: Mapped[dict] = mapped_column(JSON, default=dict)
    acceptance_criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fix_history: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="tests")
    suite: Mapped[Optional["TestSuite"]] = relationship(back_populates="tests")
    runs: Mapped[list["Run"]] = relationship(back_populates="test")


class BatchRun(Base):
    __tablename__ = "batch_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(30), default="suite")
    source_id: Mapped[int] = mapped_column(Integer, default=0)
    source_name: Mapped[str] = mapped_column(String(200), default="")
    platform: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship()
    child_runs: Mapped[list["Run"]] = relationship(back_populates="batch_run")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    build_id: Mapped[int] = mapped_column(ForeignKey("builds.id", ondelete="SET NULL"), nullable=True, index=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="SET NULL"), nullable=True, index=True)
    batch_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("batch_runs.id", ondelete="SET NULL"), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(30), default="queued")
    device_target: Mapped[str] = mapped_column(String(200), default="")
    platform: Mapped[str] = mapped_column(String(50), default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    failure_category: Mapped[str] = mapped_column(String(50), default="")
    summary: Mapped[dict] = deferred(mapped_column(JSON, default=dict))
    artifacts: Mapped[dict] = deferred(mapped_column(JSON, default=dict))

    data_set_id: Mapped[Optional[int]] = mapped_column(ForeignKey("data_sets.id", ondelete="SET NULL"), nullable=True, index=True)
    data_row_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="runs")
    build: Mapped[Optional["Build"]] = relationship(back_populates="runs")
    test: Mapped[Optional["TestDefinition"]] = relationship(back_populates="runs")
    batch_run: Mapped[Optional["BatchRun"]] = relationship(back_populates="child_runs")
    data_set: Mapped[Optional["DataSet"]] = relationship()


class DataFolder(Base):
    __tablename__ = "data_folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    project: Mapped["Project"] = relationship()
    data_sets: Mapped[list["DataSet"]] = relationship(back_populates="folder", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_data_folder_name"),
    )


class DataSet(Base):
    __tablename__ = "data_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("data_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    environment: Mapped[str] = mapped_column(String(100), default="")
    variables: Mapped[dict] = mapped_column(JSON, default=dict)
    rows: Mapped[list] = mapped_column(JSON, default=list)
    is_default: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    project: Mapped["Project"] = relationship()
    folder: Mapped[Optional["DataFolder"]] = relationship(back_populates="data_sets")

    __table_args__ = (
        UniqueConstraint("project_id", "folder_id", "name", name="uq_data_set_name"),
    )


class ScreenFolder(Base):
    __tablename__ = "screen_folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    project: Mapped["Project"] = relationship()
    screens: Mapped[list["ScreenLibrary"]] = relationship(back_populates="folder", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_screen_folder_name"),
    )


class ScreenLibrary(Base):
    __tablename__ = "screen_library"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    build_id: Mapped[Optional[int]] = mapped_column(ForeignKey("builds.id", ondelete="SET NULL"), nullable=True, index=True)
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("screen_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    xml_snapshot: Mapped[str] = deferred(mapped_column(Text, nullable=False))
    screenshot_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    captured_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    auto_captured: Mapped[bool] = mapped_column(Integer, default=0)
    screen_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    project: Mapped["Project"] = relationship()
    build: Mapped[Optional["Build"]] = relationship()
    folder: Mapped[Optional["ScreenFolder"]] = relationship(back_populates="screens")

    __table_args__ = (
        UniqueConstraint("project_id", "build_id", "name", "platform", name="uq_screen_per_build"),
    )
