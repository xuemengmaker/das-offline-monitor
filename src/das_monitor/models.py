from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisRequest(BaseModel):
    relative_path: str = Field(examples=["00/Das_data_00-00-49.tdms"])
    start_sample: int = Field(default=0, ge=0)
    n_samples: int = Field(default=10_000, ge=100, le=50_000)
    channel_start: int = Field(default=1, ge=1)
    channel_end: int = Field(default=24, ge=1, le=64)
    background_fraction: float = Field(default=0.60, gt=0.1, lt=0.95)
    local_radius: int = Field(default=2, ge=1, le=10)
    rms_z_threshold: float = Field(default=2.5, gt=0)
    local_ratio_threshold: float = Field(default=1.10, gt=1)
    spectral_z_threshold: float = Field(default=2.0, gt=0)
    guard_channels: int = Field(default=2, ge=0, le=10)
    background_iterations: int = Field(default=3, ge=1, le=10)
    frequency_low_hz: float = Field(default=2.0, ge=0)
    frequency_high_hz: float = Field(default=120.0, gt=0)

    @field_validator("relative_path")
    @classmethod
    def path_must_be_relative(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("relative_path 不能为空")
        return value.replace("\\", "/")


class PersistenceRequest(AnalysisRequest):
    """A bounded sequence of windows; alerts require repeated candidates."""

    n_windows: int = Field(default=3, ge=2, le=10)
    window_step_samples: int | None = Field(default=None, ge=100, le=50_000)
    min_persistent_windows: int = Field(default=2, ge=2, le=10)


class FilePersistenceRequest(AnalysisRequest):
    """Analyse sequential TDMS files beginning with ``relative_path``."""

    n_files: int = Field(default=3, ge=2, le=10)
    min_persistent_files: int = Field(default=2, ge=2, le=10)


class JobView(BaseModel):
    job_id: str
    status: JobStatus
    request: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str
    updated_at: str
