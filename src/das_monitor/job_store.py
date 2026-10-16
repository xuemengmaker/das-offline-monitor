from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import JobStatus, JobView
from .settings import settings


def _now() -> str:
    return datetime.now(UTC).isoformat()


class JobStore:
    """Small SQLite repository; enough for a single-user local replay console."""

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path or settings.database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def create(self, request: dict[str, object]) -> JobView:
        job_id = f"job-{uuid4().hex[:12]}"
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO analysis_jobs VALUES (?, ?, ?, NULL, NULL, ?, ?)",
                (job_id, JobStatus.QUEUED, json.dumps(request, ensure_ascii=False), now, now),
            )
        return self.get(job_id)

    def set_status(self, job_id: str, status: JobStatus, *, result: dict[str, object] | None = None, error: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE analysis_jobs SET status=?, result_json=?, error=?, updated_at=? WHERE job_id=?",
                (status, json.dumps(result, ensure_ascii=False) if result else None, error, _now(), job_id),
            )

    def get(self, job_id: str) -> JobView:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT job_id, status, request_json, result_json, error, created_at, updated_at "
                "FROM analysis_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return JobView(
            job_id=row[0],
            status=JobStatus(row[1]),
            request=json.loads(row[2]),
            result=json.loads(row[3]) if row[3] else None,
            error=row[4],
            created_at=row[5],
            updated_at=row[6],
        )

    def recent(self, limit: int = 20) -> list[JobView]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id, status, request_json, result_json, error, created_at, updated_at "
                "FROM analysis_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            JobView(
                job_id=row[0], status=JobStatus(row[1]), request=json.loads(row[2]),
                result=json.loads(row[3]) if row[3] else None, error=row[4], created_at=row[5], updated_at=row[6]
            )
            for row in rows
        ]
