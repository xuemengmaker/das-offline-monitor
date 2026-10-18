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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS data_assets (
                    asset_id TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    integrity_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_heartbeats (
                    device_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    received_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS alarm_events (
                    event_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS alarm_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    callback_url TEXT,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    last_error TEXT,
                    response_code INTEGER,
                    created_at TEXT NOT NULL
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

    def register_asset(
        self,
        relative_path: str,
        metadata: dict[str, object],
        integrity: dict[str, object],
    ) -> dict[str, object]:
        """Idempotently register the same file version, while retaining changed versions."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT asset_id, metadata_json, integrity_json, created_at FROM data_assets "
                "WHERE relative_path=? ORDER BY created_at DESC LIMIT 1",
                (relative_path,),
            ).fetchone()
            if row and json.loads(row[2]) == integrity:
                return {
                    "asset_id": row[0],
                    "relative_path": relative_path,
                    "metadata": json.loads(row[1]),
                    "integrity": json.loads(row[2]),
                    "deduplicated": True,
                    "created_at": row[3],
                }
            asset_id = f"asset-{uuid4().hex[:12]}"
            created_at = _now()
            connection.execute(
                "INSERT INTO data_assets VALUES (?, ?, ?, ?, ?)",
                (
                    asset_id,
                    relative_path,
                    json.dumps(metadata, ensure_ascii=False),
                    json.dumps(integrity, ensure_ascii=False),
                    created_at,
                ),
            )
        return {
            "asset_id": asset_id,
            "relative_path": relative_path,
            "metadata": metadata,
            "integrity": integrity,
            "deduplicated": False,
            "created_at": created_at,
        }

    def get_asset(self, asset_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT asset_id, relative_path, metadata_json, integrity_json, created_at "
                "FROM data_assets WHERE asset_id=?",
                (asset_id,),
            ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return {
            "asset_id": row[0],
            "relative_path": row[1],
            "metadata": json.loads(row[2]),
            "integrity": json.loads(row[3]),
            "deduplicated": False,
            "created_at": row[4],
        }

    def record_heartbeat(self, payload: dict[str, object]) -> dict[str, object]:
        received_at = _now()
        device_id = str(payload["device_id"])
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO device_heartbeats VALUES (?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET payload_json=excluded.payload_json, received_at=excluded.received_at",
                (device_id, json.dumps(payload, ensure_ascii=False), received_at),
            )
        return {**payload, "received_at": received_at}

    def get_heartbeat(self, device_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json, received_at FROM device_heartbeats WHERE device_id=?", (device_id,)
            ).fetchone()
        if row is None:
            raise KeyError(device_id)
        return {**json.loads(row[0]), "received_at": row[1]}

    def create_alarm_events(self, job_id: str, result: dict[str, object]) -> list[dict[str, object]]:
        alerts = result.get("alerts", [])
        if not isinstance(alerts, list):
            return []
        created_at = _now()
        events: list[dict[str, object]] = []
        with self._connect() as connection:
            for alert in alerts:
                if not isinstance(alert, dict):
                    continue
                event = {
                    "event_id": f"evt-{uuid4().hex[:12]}",
                    "job_id": job_id,
                    "event_type": "das_leakage_risk_alert",
                    "payload": {
                        "analysis_scope": result.get("analysis_scope"),
                        "relative_path": result.get("relative_path"),
                        "relative_paths": result.get("relative_paths"),
                        "alert": alert,
                    },
                    "created_at": created_at,
                }
                connection.execute(
                    "INSERT INTO alarm_events VALUES (?, ?, ?, ?, ?)",
                    (
                        event["event_id"],
                        job_id,
                        event["event_type"],
                        json.dumps(event["payload"], ensure_ascii=False),
                        created_at,
                    ),
                )
                events.append(event)
        return events

    def record_delivery(
        self,
        event_id: str,
        callback_url: str | None,
        status: str,
        attempts: int,
        *,
        last_error: str | None = None,
        response_code: int | None = None,
    ) -> dict[str, object]:
        delivery = {
            "delivery_id": f"delivery-{uuid4().hex[:12]}",
            "event_id": event_id,
            "callback_url": callback_url,
            "status": status,
            "attempts": attempts,
            "last_error": last_error,
            "response_code": response_code,
            "created_at": _now(),
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO alarm_deliveries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(delivery.values()),
            )
        return delivery

    def recent_alarm_events(self, limit: int = 50) -> list[dict[str, object]]:
        with self._connect() as connection:
            events = connection.execute(
                "SELECT event_id, job_id, event_type, payload_json, created_at FROM alarm_events "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            deliveries = connection.execute(
                "SELECT delivery_id, event_id, callback_url, status, attempts, last_error, response_code, created_at "
                "FROM alarm_deliveries"
            ).fetchall()
        by_event: dict[str, list[dict[str, object]]] = {}
        for item in deliveries:
            by_event.setdefault(item[1], []).append(
                {
                    "delivery_id": item[0], "event_id": item[1], "callback_url": item[2], "status": item[3],
                    "attempts": item[4], "last_error": item[5], "response_code": item[6], "created_at": item[7],
                }
            )
        return [
            {
                "event_id": item[0], "job_id": item[1], "event_type": item[2],
                "payload": json.loads(item[3]), "created_at": item[4], "deliveries": by_event.get(item[0], []),
            }
            for item in events
        ]
