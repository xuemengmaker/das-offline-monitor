from __future__ import annotations

import allure
import numpy as np
import pytest
from fastapi.testclient import TestClient
from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter

from das_monitor import main
from das_monitor.job_store import JobStore
from das_monitor.settings import settings


@pytest.fixture
def client_with_tdms(tmp_path, monkeypatch):
    """An isolated API test environment: temporary TDMS data and temporary SQLite database."""
    monkeypatch.setattr(settings, "data_root", tmp_path)
    monkeypatch.setattr(main, "store", JobStore(tmp_path / "test_jobs.db"))
    with TdmsWriter(tmp_path / "demo.tdms") as writer:
        writer.write_segment(
            [
                RootObject(properties={"channels": 2, "samples": 200, "sample_ratio": 1000.0}),
                GroupObject("data"),
                ChannelObject("data", "flattened", np.concatenate([np.ones(200), np.full(200, 2.0)])),
            ]
        )
    with TestClient(main.app) as client:
        yield client


def valid_payload(**overrides):
    payload = {
        "relative_path": "demo.tdms",
        "start_sample": 0,
        "n_samples": 100,
        "channel_start": 1,
        "channel_end": 2,
        "background_fraction": 0.6,
    }
    payload.update(overrides)
    return payload


@allure.feature("DAS API 回归")
@allure.story("正常离线回放任务")
def test_api_creates_and_completes_analysis_job(client_with_tdms):
    response = client_with_tdms.post("/api/v1/analysis-jobs", json=valid_payload())

    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "queued"

    result_response = client_with_tdms.get(f"/api/v1/analysis-jobs/{job['job_id']}")
    assert result_response.status_code == 200
    result = result_response.json()
    assert result["status"] == "completed"
    assert result["result"]["window"]["channel_count"] == 2


@allure.feature("DAS API 回归")
@allure.story("接口参数边界")
def test_api_rejects_too_many_samples_before_creating_job(client_with_tdms):
    response = client_with_tdms.post("/api/v1/analysis-jobs", json=valid_payload(n_samples=50_001))

    assert response.status_code == 422
    assert "less than or equal to 50000" in response.text


@allure.feature("DAS API 回归")
@allure.story("后台读取边界")
def test_api_marks_out_of_range_window_as_failed_job(client_with_tdms):
    response = client_with_tdms.post("/api/v1/analysis-jobs", json=valid_payload(start_sample=200))

    assert response.status_code == 202
    job = client_with_tdms.get(f"/api/v1/analysis-jobs/{response.json()['job_id']}").json()
    assert job["status"] == "failed"
    assert "采样窗口超过单通道长度 200" in job["error"]


@allure.feature("DAS API 回归")
@allure.story("持续性预警")
def test_api_creates_persistence_job(client_with_tdms):
    response = client_with_tdms.post(
        "/api/v1/persistence-jobs",
        json={**valid_payload(), "n_windows": 2, "window_step_samples": 100, "min_persistent_windows": 2},
    )

    assert response.status_code == 202
    job = client_with_tdms.get(f"/api/v1/analysis-jobs/{response.json()['job_id']}").json()
    assert job["status"] == "completed"
    assert job["result"]["analysis_scope"] == "multi_window_persistence_screening"
