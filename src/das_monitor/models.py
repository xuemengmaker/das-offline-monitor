from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisRequest(BaseModel):
    relative_path: str | None = Field(default=None, examples=["00/Das_data_00-00-49.tdms"])
    data_asset_id: str | None = Field(
        default=None,
        description="由数据接入接口返回的资产 ID；与 relative_path 二选一。",
    )
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
    alarm_callback_url: str | None = Field(
        default=None,
        description="可选的本地/受控告警接收端 HTTP URL。生产环境应配置白名单与认证。",
    )

    @field_validator("relative_path")
    @classmethod
    def path_must_be_relative(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("relative_path 不能为空")
        return value.replace("\\", "/")

    @field_validator("alarm_callback_url")
    @classmethod
    def callback_url_must_be_http(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("alarm_callback_url 必须以 http:// 或 https:// 开头")
        return value

    @model_validator(mode="after")
    def source_must_be_unambiguous(self) -> AnalysisRequest:
        if bool(self.relative_path) == bool(self.data_asset_id):
            raise ValueError("relative_path 与 data_asset_id 必须且只能提供一个")
        return self


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


class AssetRegisterRequest(BaseModel):
    """Register an existing TDMS file under the configured read-only data root."""

    relative_path: str
    integrity_mode: Literal["metadata", "sha256"] = "metadata"

    @field_validator("relative_path")
    @classmethod
    def asset_path_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("relative_path 不能为空")
        return value.replace("\\", "/")


class DataAssetView(BaseModel):
    asset_id: str
    relative_path: str
    metadata: dict[str, Any]
    integrity: dict[str, Any]
    deduplicated: bool
    created_at: str


class DeviceHeartbeatRequest(BaseModel):
    """Local collector simulator contract; not a claim of real hardware protocol support."""

    device_id: str = Field(min_length=1, max_length=64)
    status: Literal["online", "offline", "degraded"]
    channel_count: int = Field(ge=1, le=100_000)
    sampling_rate_hz: float = Field(gt=0, le=10_000_000)
    latest_relative_path: str | None = None
    observed_at: str | None = None

    @field_validator("latest_relative_path")
    @classmethod
    def normalize_optional_path(cls, value: str | None) -> str | None:
        return value.replace("\\", "/") if value else value


class DeviceHeartbeatView(DeviceHeartbeatRequest):
    received_at: str


class AlarmDeliveryView(BaseModel):
    delivery_id: str
    event_id: str
    callback_url: str | None
    status: str
    attempts: int
    last_error: str | None = None
    response_code: int | None = None
    created_at: str


class AlarmEventView(BaseModel):
    event_id: str
    job_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: str
    deliveries: list[AlarmDeliveryView] = []
