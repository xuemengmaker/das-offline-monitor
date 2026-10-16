from __future__ import annotations

import asyncio
import csv
import io
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from .job_store import JobStore
from .logging_config import logger
from .models import AnalysisRequest, FilePersistenceRequest, JobStatus, JobView, PersistenceRequest
from .settings import settings
from .tdms_service import (
    DataAccessError,
    analyse_file_persistence,
    analyse_persistence,
    analyse_window,
    list_tdms_files,
    read_metadata,
)

store = JobStore()
WEB_ROOT = Path(__file__).parent / "web"


async def execute_job(job_id: str, request: AnalysisRequest) -> None:
    store.set_status(job_id, JobStatus.RUNNING)
    logger.info(
        "event=job_started job_id=%s file=%s start=%s samples=%s channels=%s-%s",
        job_id, request.relative_path, request.start_sample, request.n_samples, request.channel_start, request.channel_end,
    )
    started_at = time.perf_counter()
    try:
        result = await asyncio.to_thread(analyse_window, **request.model_dump())
    except (DataAccessError, OSError, ValueError) as exc:
        logger.exception("event=job_failed job_id=%s error=%s", job_id, exc)
        store.set_status(job_id, JobStatus.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - API task boundary: preserve traceback, expose only a safe error
        logger.exception("event=job_failed_unexpected job_id=%s", job_id)
        store.set_status(job_id, JobStatus.FAILED, error=f"内部分析错误: {type(exc).__name__}")
    else:
        store.set_status(job_id, JobStatus.COMPLETED, result=result)
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
        result = await asyncio.to_thread(analyse_persistence, **request.model_dump())
    except (DataAccessError, OSError, ValueError) as exc:
        logger.exception("event=persistence_job_failed job_id=%s error=%s", job_id, exc)
        store.set_status(job_id, JobStatus.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - API task boundary: preserve traceback, expose only a safe error
        logger.exception("event=persistence_job_failed_unexpected job_id=%s", job_id)
        store.set_status(job_id, JobStatus.FAILED, error=f"内部分析错误: {type(exc).__name__}")
    else:
        store.set_status(job_id, JobStatus.COMPLETED, result=result)
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
        result = await asyncio.to_thread(analyse_file_persistence, **request.model_dump())
    except (DataAccessError, OSError, ValueError) as exc:
        logger.exception("event=file_persistence_job_failed job_id=%s error=%s", job_id, exc)
        store.set_status(job_id, JobStatus.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - API task boundary: preserve traceback, expose only a safe error
        logger.exception("event=file_persistence_job_failed_unexpected job_id=%s", job_id)
        store.set_status(job_id, JobStatus.FAILED, error=f"内部分析错误: {type(exc).__name__}")
    else:
        store.set_status(job_id, JobStatus.COMPLETED, result=result)
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
        read_metadata(request.relative_path)
    except DataAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = store.create(request.model_dump())
    background_tasks.add_task(execute_job, job.job_id, request)
    return job


@app.post("/api/v1/persistence-jobs", response_model=JobView, status_code=202)
async def create_persistence_job(request: PersistenceRequest, background_tasks: BackgroundTasks) -> JobView:
    try:
        read_metadata(request.relative_path)
    except DataAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = store.create(request.model_dump())
    background_tasks.add_task(execute_persistence_job, job.job_id, request)
    return job


@app.post("/api/v1/file-persistence-jobs", response_model=JobView, status_code=202)
async def create_file_persistence_job(request: FilePersistenceRequest, background_tasks: BackgroundTasks) -> JobView:
    try:
        read_metadata(request.relative_path)
    except DataAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = store.create(request.model_dump())
    background_tasks.add_task(execute_file_persistence_job, job.job_id, request)
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
