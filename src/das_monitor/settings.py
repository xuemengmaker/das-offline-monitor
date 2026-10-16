from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Environment variables override these safe local defaults."""

    model_config = SettingsConfigDict(env_prefix="DAS_", extra="ignore")

    data_root: Path = Path("/mnt/f/数据")
    runtime_dir: Path = Path("runtime")
    log_dir: Path = Path("logs")
    max_channels: int = 64
    max_samples_per_job: int = 50_000
    default_samples: int = 10_000

    @property
    def database_path(self) -> Path:
        return self.runtime_dir / "das_monitor.db"


settings = Settings()
