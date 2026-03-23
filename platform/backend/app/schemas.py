from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


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
    data_set_id: Optional[int] = None
    environment: Optional[str] = None


class RunOut(BaseModel):
    id: int
    project_id: int
    build_id: Optional[int]
    test_id: Optional[int]
    batch_run_id: Optional[int] = None
    status: str
    platform: str
    device_target: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    error_message: Optional[str]
    summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    data_set_id: Optional[int] = None
    data_row_index: Optional[int] = None


# ── Batch Run ─────────────────────────────────────────────────────────

class BatchRunCreate(BaseModel):
    project_id: int
    build_id: Optional[int] = None
    mode: str  # suite|collection
    source_id: int  # suite_id or module_id
    platform: str
    device_target: str = ""
    data_set_id: Optional[int] = None
    environment: Optional[str] = None

class BatchRunChildOut(BaseModel):
    run_id: int
    test_id: int
    test_name: str
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None

class BatchRunOut(BaseModel):
    id: int
    project_id: int
    mode: str
    source_id: int
    source_name: str
    platform: str
    status: str
    total: int
    passed: int
    failed: int
    build_id: Optional[int] = None
    device_target: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    children: list[BatchRunChildOut] = Field(default_factory=list)


# ── Screen session / capture ──────────────────────────────────────────
# device_target: only chars safe to pass as argv tokens (adb / simctl), no shell metacharacters.

_DEVICE_TARGET_PATTERN = r"^[\w.\-:@/]*$"


class StartScreenSessionBody(BaseModel):
    project_id: int
    build_id: int
    folder_id: int
    platform: str = "android"
    device_target: str = Field(default="", max_length=512, pattern=_DEVICE_TARGET_PATTERN)

    @field_validator("device_target", mode="before")
    @classmethod
    def strip_device_target(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v).strip()


class StopScreenSessionBody(BaseModel):
    project_id: int
    build_id: int
    platform: str = "android"
    device_target: str = Field(default="", max_length=512, pattern=_DEVICE_TARGET_PATTERN)

    @field_validator("device_target", mode="before")
    @classmethod
    def strip_device_target(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v).strip()


class CaptureScreenBody(BaseModel):
    project_id: int
    build_id: int
    folder_id: int
    name: str = Field(min_length=1, max_length=500)
    platform: str = "android"
    notes: str = ""
    device_target: str = Field(default="", max_length=512, pattern=_DEVICE_TARGET_PATTERN)

    @field_validator("notes", mode="before")
    @classmethod
    def coerce_notes(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v)

    @field_validator("device_target", mode="before")
    @classmethod
    def strip_device_target(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v


# ── Data Layer ────────────────────────────────────────────────────────

class DataFolderCreate(BaseModel):
    project_id: int
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class DataFolderOut(BaseModel):
    id: int
    project_id: int
    name: str
    description: str
    data_set_count: int = 0
    created_at: Optional[datetime] = None


class DataSetCreate(BaseModel):
    project_id: int
    folder_id: Optional[int] = None
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    environment: str = ""
    variables: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    is_default: bool = False


class DataSetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    environment: Optional[str] = None
    folder_id: Optional[int] = None
    variables: Optional[dict[str, Any]] = None
    rows: Optional[list[dict[str, Any]]] = None
    is_default: Optional[bool] = None


class DataSetOut(BaseModel):
    id: int
    project_id: int
    folder_id: Optional[int]
    name: str
    description: str
    environment: str
    variables: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
