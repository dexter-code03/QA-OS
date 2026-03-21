from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectOut(BaseModel):
    id: int
    name: str
    created_at: datetime


# ── Module / Suite hierarchy ──────────────────────────────────────────

class ModuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ModuleOut(BaseModel):
    id: int
    project_id: int
    name: str
    created_at: datetime


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SuiteOut(BaseModel):
    id: int
    module_id: int
    name: str
    created_at: datetime


# ── Build ─────────────────────────────────────────────────────────────

class BuildOut(BaseModel):
    id: int
    project_id: int
    platform: str
    file_name: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Test ──────────────────────────────────────────────────────────────

class TestCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    platform_steps: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    suite_id: Optional[int] = None
    prerequisite_test_id: Optional[int] = None
    acceptance_criteria: Optional[str] = None


class TestUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    steps: Optional[list[dict[str, Any]]] = None
    platform_steps: Optional[dict[str, list[dict[str, Any]]]] = None
    platform: Optional[str] = None  # android | ios_sim — which slot steps update applies to
    suite_id: Optional[int] = None
    prerequisite_test_id: Optional[int] = None
    acceptance_criteria: Optional[str] = None


class TestOut(BaseModel):
    id: int
    project_id: int
    suite_id: Optional[int] = None
    prerequisite_test_id: Optional[int] = None
    name: str
    steps: list[dict[str, Any]]
    platform_steps: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    acceptance_criteria: Optional[str] = None
    fix_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


# ── Run ───────────────────────────────────────────────────────────────

class RunCreate(BaseModel):
    project_id: int
    build_id: Optional[int] = None
    test_id: int
    platform: str  # android | ios_sim
    device_target: str = ""


class RunOut(BaseModel):
    id: int
    project_id: int
    build_id: Optional[int]
    test_id: Optional[int]
    status: str
    platform: str
    device_target: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    error_message: Optional[str]
    summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
