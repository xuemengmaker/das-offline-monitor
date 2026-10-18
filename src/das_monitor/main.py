from __future__ import annotations

import asyncio
import csv
import io
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse

from .alarm_dispatcher import deliver_event
from .job_store import JobStore
from .logging_config import logger
from .models import (
    AlarmEventView,
    AnalysisRequest,
    AssetRegisterRequest,
    DataAssetView,
    DeviceHeartbeatRequest,
    DeviceHeartbeatView,
    FilePersistenceRequest,
    JobStatus,
    JobView,
    PersistenceRequest,
)
from .settings import settings
from .tdms_service import (
    DataAccessError,
    analyse_file_persistence,
    analyse_persistence,
    analyse_window,
    build_data_asset_manifest,
    list_tdms_files,
    read_metadata,
)

store = JobStore()
WEB_ROOT = Path(__file__).parent / "web"


def _resolve_request_source(request: AnalysisRequest) -> tuple[AnalysisRequest, str | None]:
    """Map a registered data asset to a read-only relative TDMS path."""
    if request.data_asset_id is None:
        return request, None
    try:
        asset = store.get_asset(request.data_asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="数据资产不存在") from exc
    return request.model_copy(update={"relative_path": asset["relative_path"], "data_asset_id": None}), request.data_asset_id


def _analysis_kwargs(request: AnalysisRequest) -> dict[str, object]:
    """Keep API orchestration fields out of pure signal-analysis functions."""
    return request.model_dump(exclude={"data_asset_id", "alarm_callback_url"}, exclude_none=True)


async def _publish_alarm_events(job_id: str, result: dict[str, object], callback_url: str | None) -> None:
    """Persist alarm events first, then deliver them without changing analysis outcome."""
    events = store.create_alarm_events(job_id, result)
    for event in events:
        if callback_url is None:
            store.record_delivery(event["event_id"], None, "not_configured", 0)
            continue
        outcome = await asyncio.to_thread(deliver_event, callback_url, event)
        store.record_delivery(
            event["event_id"],
            callback_url,
            str(outcome["status"]),
            int(outcome["attempts"]),
            last_error=outcome.get("last_error") if isinstance(outcome.get("last_error"), str) else None,
            response_code=outcome.get("response_code") if isinstance(outcome.get("response_code"), int) else None,
        )
        logger.info(
            "event=alarm_delivery event_id=%s status=%s attempts=%s",
            event["event_id"], outcome["status"], outcome["attempts"],
        )


async def execute_job(job_id: str, request: AnalysisRequest) -> None:
    store.set_status(job_id, JobStatus.RUNNING)
    logger.info(
        "event=job_started job_id=%s file=%s start=%s samples=%s channels=%s-%s",
        job_id, request.relative_path, request.start_sample, request.n_samples, request.channel_start, request.channel_end,
    )
    started_at = time.perf_counter()
    try:
        result = await asyncio.to_thread(analyse_window, **_analysis_kwargs(request))
    except (DataAccessError, OSError, ValueError) as exc:
        logger.exception("event=job_failed job_id=%s error=%s", job_id, exc)
        store.set_status(job_id, JobStatus.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - API task boundary: preserve traceback, expose only a safe error
        logger.exception("event=job_failed_unexpected job_id=%s", job_id)
        store.set_status(job_id, JobStatus.FAILED, error=f"内部分析错误: {type(exc).__name__}")
    else:
        store.set_status(job_id, JobStatus.COMPLETED, result=result)
        await _publish_alarm_events(job_id, result, request.alarm_callback_url)
        logger.info(
            "event=job_completed job_id=%s candidates=%s elapsed_ms=%s",
            job_id, result["summary"]["candidate_count"], round((time.perf_counter() - started_at) * 1000),
        )


async def execute_persistence_job(job_id: str, request: PersistenceRequest) -> None:
    store.set_status(job_id, JobStatus.RUNNING)
    logger.info(
        "event=persistence_job_started job_id=%s file=%s windows=%s samples=%s channels=%s-%s",
        job_id, request.relative_path, request.n_windows, request.n_samples, request.channel_start, request.channel_end,
    )
    started_at = time.perf_counter()
    try:
        result = await asyncio.to_thread(analyse_persistence, **_analysis_kwargs(request))
    except (DataAccessError, OSError, ValueError) as exc:
        logger.exception("event=persistence_job_failed job_id=%s error=%s", job_id, exc)
        store.set_status(job_id, JobStatus.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - API task boundary: preserve traceback, expose only a safe error
        logger.exception("event=persistence_job_failed_unexpected job_id=%s", job_id)
        store.set_status(job_id, JobStatus.FAILED, error=f"内部分析错误: {type(exc).__name__}")
    else:
        store.set_status(job_id, JobStatus.COMPLETED, result=result)
        await _publish_alarm_events(job_id, result, request.alarm_callback_url)
        logger.info(
            "event=persistence_job_completed job_id=%s alerts=%s elapsed_ms=%s",
            job_id, len(result["alerts"]), round((time.perf_counter() - started_at) * 1000),
        )


async def execute_file_persistence_job(job_id: str, request: FilePersistenceRequest) -> None:
    store.set_status(job_id, JobStatus.RUNNING)
    logger.info(
        "event=file_persistence_job_started job_id=%s file=%s files=%s samples=%s channels=%s-%s",
        job_id, request.relative_path, request.n_files, request.n_samples, request.channel_start, request.channel_end,
    )
    started_at = time.perf_counter()
    try:
        result = await asyncio.to_thread(analyse_file_persistence, **_analysis_kwargs(request))
    except (DataAccessError, OSError, ValueError) as exc:
        logger.exception("event=file_persistence_job_failed job_id=%s error=%s", job_id, exc)
        store.set_status(job_id, JobStatus.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - API task boundary: preserve traceback, expose only a safe error
        logger.exception("event=file_persistence_job_failed_unexpected job_id=%s", job_id)
        store.set_status(job_id, JobStatus.FAILED, error=f"内部分析错误: {type(exc).__name__}")
    else:
        store.set_status(job_id, JobStatus.COMPLETED, result=result)
        await _publish_alarm_events(job_id, result, request.alarm_callback_url)
        logger.info(
            "event=file_persistence_job_completed job_id=%s alerts=%s elapsed_ms=%s",
            job_id, len(result["alerts"]), round((time.perf_counter() - started_at) * 1000),
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.info("event=service_started data_root=%s", settings.data_root)
    yield
    logger.info("event=service_stopped")


app = FastAPI(
    title="DAS 离线回放监测与自动背景分级预警系统",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/api/v1/health")
def health() -> dict[str, object]:
    return {"status": "ok", "data_root_available": settings.data_root.is_dir()}


@app.get("/api/v1/tdms/files")
def files(limit: int = 100) -> dict[str, object]:
    try:
        return {"files": list_tdms_files(min(max(limit, 1), 500))}
    except DataAccessError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/data-assets/register", response_model=DataAssetView, status_code=201)
def register_data_asset(request: AssetRegisterRequest, response: Response) -> dict[str, object]:
    """Accept a collector/file handoff by registering a validated existing TDMS asset."""
    try:
        manifest = build_data_asset_manifest(request.relative_path, request.integrity_mode)
    except DataAccessError as exc:
        logger.warning("event=data_asset_rejected path=%s reason=%s", request.relative_path, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    asset = store.register_asset(request.relative_path, manifest["metadata"], manifest["integrity"])
    if asset["deduplicated"]:
        response.status_code = 200
    logger.info(
        "event=data_asset_registered asset_id=%s path=%s deduplicated=%s",
        asset["asset_id"], request.relative_path, asset["deduplicated"],
    )
    return asset


@app.get("/api/v1/data-assets/{asset_id}", response_model=DataAssetView)
def get_data_asset(asset_id: str) -> dict[str, object]:
    try:
        return store.get_asset(asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="数据资产不存在") from exc


@app.post("/api/v1/devices/heartbeats", response_model=DeviceHeartbeatView, status_code=202)
def receive_device_heartbeat(request: DeviceHeartbeatRequest) -> dict[str, object]:
    """Local collector simulator contract, separate from a vendor hardware protocol."""
    payload = request.model_dump()
    record = store.record_heartbeat(payload)
    logger.info(
        "event=device_heartbeat device_id=%s status=%s channels=%s",
        request.device_id, request.status, request.channel_count,
    )
    return record


@app.get("/api/v1/devices/{device_id}", response_model=DeviceHeartbeatView)
def get_device_heartbeat(device_id: str) -> dict[str, object]:
    try:
        return store.get_heartbeat(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="设备状态不存在") from exc


@app.get("/api/v1/alarm-events", response_model=list[AlarmEventView])
def list_alarm_events(limit: int = 50) -> list[dict[str, object]]:
    return store.recent_alarm_events(min(max(limit, 1), 200))


@app.get("/api/v1/tdms/metadata")
def metadata(relative_path: str) -> dict[str, object]:
    try:
        return read_metadata(relative_path)
    except DataAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/analysis-jobs", response_model=JobView, status_code=202)
async def create_job(request: AnalysisRequest, background_tasks: BackgroundTasks) -> JobView:
    try:
        # Validate the path before accepting a task; data is not read yet.
        resolved_request, asset_id = _resolve_request_source(request)
        read_metadata(str(resolved_request.relative_path))
    except DataAccessError as exc:
        logger.warning("event=analysis_request_rejected reason=%s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_request = resolved_request.model_dump()
    job_request["data_asset_id"] = asset_id
    job = store.create(job_request)
    background_tasks.add_task(execute_job, job.job_id, resolved_request)
    return job


@app.post("/api/v1/persistence-jobs", response_model=JobView, status_code=202)
async def create_persistence_job(request: PersistenceRequest, background_tasks: BackgroundTasks) -> JobView:
    try:
        resolved_request, asset_id = _resolve_request_source(request)
        read_metadata(str(resolved_request.relative_path))
    except DataAccessError as exc:
        logger.warning("event=persistence_request_rejected reason=%s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_request = resolved_request.model_dump()
    job_request["data_asset_id"] = asset_id
    job = store.create(job_request)
    background_tasks.add_task(execute_persistence_job, job.job_id, resolved_request)
    return job


@app.post("/api/v1/file-persistence-jobs", response_model=JobView, status_code=202)
async def create_file_persistence_job(request: FilePersistenceRequest, background_tasks: BackgroundTasks) -> JobView:
    try:
        resolved_request, asset_id = _resolve_request_source(request)
        read_metadata(str(resolved_request.relative_path))
    except DataAccessError as exc:
        logger.warning("event=file_persistence_request_rejected reason=%s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_request = resolved_request.model_dump()
    job_request["data_asset_id"] = asset_id
    job = store.create(job_request)
    background_tasks.add_task(execute_file_persistence_job, job.job_id, resolved_request)
    return job


@app.get("/api/v1/analysis-jobs", response_model=list[JobView])
def list_jobs() -> list[JobView]:
    return store.recent()


@app.get("/api/v1/analysis-jobs/{job_id}", response_model=JobView)
def get_job(job_id: str) -> JobView:
    try:
        return store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="分析任务不存在") from exc


@app.get("/api/v1/analysis-jobs/{job_id}/channel-metrics.csv")
def export_channel_metrics(job_id: str) -> StreamingResponse:
    """Export a completed single-file screening result without copying raw TDMS data."""
    try:
        job = store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="分析任务不存在") from exc
    metrics = (job.result or {}).get("channel_metrics")
    if job.status != JobStatus.COMPLETED or not isinstance(metrics, list) or not metrics:
        raise HTTPException(status_code=409, detail="该任务没有可导出的逐通道分析结果")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(metrics[0].keys()))
    writer.writeheader()
    writer.writerows(metrics)
    headers = {"Content-Disposition": f'attachment; filename="{job_id}-channel-metrics.csv"'}
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv; charset=utf-8", headers=headers)
