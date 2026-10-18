from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import allure
import numpy as np
import pytest
from fastapi.testclient import TestClient
from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter

from das_monitor import main
from das_monitor.job_store import JobStore
from das_monitor.settings import settings


@pytest.fixture
def interface_client(tmp_path, monkeypatch):
    """Isolated collector-to-monitoring-system integration boundary."""
    monkeypatch.setattr(settings, "data_root", tmp_path)
    monkeypatch.setattr(main, "store", JobStore(tmp_path / "interface_jobs.db"))
    with TdmsWriter(tmp_path / "normal.tdms") as writer:
        writer.write_segment(
            [
                RootObject(properties={"channels": 2, "samples": 300, "sample_ratio": 1000.0}),
                GroupObject("data"),
                ChannelObject("data", "flattened", np.concatenate([np.ones(300), np.full(300, 1.2)])),
            ]
        )
    with TestClient(main.app) as client:
        yield client, tmp_path


def persistent_tdms(path):
    # Keep the independently verified signal shape used by the algorithm suite.
    rng = np.random.default_rng(42)
    samples = 300
    time_axis = np.arange(samples) / 1000.0
    channels = [1.0 + 0.02 * rng.standard_normal(samples) for _ in range(5)]
    channels.append(1.0 + 0.02 * rng.standard_normal(samples) + 0.8 * np.sin(2 * np.pi * 40 * time_axis))
    with TdmsWriter(path) as writer:
        writer.write_segment(
            [
                RootObject(properties={"channels": 6, "samples": samples, "sample_ratio": 1000.0}),
                GroupObject("data"),
                ChannelObject("data", "flattened", np.concatenate(channels)),
            ]
        )


def persistent_payload(**overrides):
    payload = {
        "relative_path": "persistent.tdms",
        "start_sample": 0,
        "n_samples": 100,
        "channel_start": 1,
        "channel_end": 6,
        "background_fraction": 0.6,
        "n_windows": 3,
        "window_step_samples": 100,
        "min_persistent_windows": 2,
    }
    payload.update(overrides)
    return payload


class _Receiver(BaseHTTPRequestHandler):
    responses_before_success: ClassVar[int] = 0
    received: ClassVar[list[dict[str, object]]] = []

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).received.append({"headers": dict(self.headers), "body": json.loads(raw)})
        if len(type(self).received) <= type(self).responses_before_success:
            self.send_response(503)
        else:
            self.send_response(204)
        self.end_headers()

    def log_message(self, *_):
        return


@pytest.fixture
def alarm_receiver():
    _Receiver.received = []
    _Receiver.responses_before_success = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _Receiver
    finally:
        server.shutdown()
        thread.join(timeout=2)


@allure.feature("数据接入接口")
def test_register_tdms_asset_returns_metadata_manifest(interface_client):
    client, _ = interface_client
    response = client.post("/api/v1/data-assets/register", json={"relative_path": "normal.tdms"})

    assert response.status_code == 201
    asset = response.json()
    assert asset["asset_id"].startswith("asset-")
    assert asset["metadata"]["logical_channel_count"] == 2
    assert asset["integrity"]["mode"] == "metadata"
    assert asset["integrity"]["bytes"] > 0


@allure.feature("数据接入接口")
def test_re_registering_same_asset_is_idempotent(interface_client):
    client, _ = interface_client
    first = client.post("/api/v1/data-assets/register", json={"relative_path": "normal.tdms"}).json()
    second = client.post("/api/v1/data-assets/register", json={"relative_path": "normal.tdms"})

    assert second.status_code == 200
    assert second.json()["asset_id"] == first["asset_id"]
    assert second.json()["deduplicated"] is True


@allure.feature("数据接入接口")
def test_sha256_asset_intake_records_full_integrity_fingerprint(interface_client):
    client, _ = interface_client
    response = client.post(
        "/api/v1/data-assets/register", json={"relative_path": "normal.tdms", "integrity_mode": "sha256"}
    )

    assert response.status_code == 201
    assert len(response.json()["integrity"]["sha256"]) == 64


@allure.feature("数据接入接口")
def test_asset_id_can_drive_analysis_without_exposing_filesystem_path(interface_client):
    client, _ = interface_client
    asset = client.post("/api/v1/data-assets/register", json={"relative_path": "normal.tdms"}).json()
    response = client.post(
        "/api/v1/analysis-jobs",
        json={
            "data_asset_id": asset["asset_id"], "start_sample": 0, "n_samples": 100,
            "channel_start": 1, "channel_end": 2, "background_fraction": 0.6,
        },
    )

    assert response.status_code == 202
    job = client.get(f"/api/v1/analysis-jobs/{response.json()['job_id']}").json()
    assert job["status"] == "completed"
    assert job["request"]["data_asset_id"] == asset["asset_id"]


@allure.feature("数据接入接口")
def test_asset_intake_rejects_path_traversal(interface_client):
    client, _ = interface_client
    response = client.post("/api/v1/data-assets/register", json={"relative_path": "../private.tdms"})

    assert response.status_code == 400
    assert "不含 .." in response.json()["detail"]


@allure.feature("采集端状态接口")
def test_device_heartbeat_is_upserted_and_queryable(interface_client):
    client, _ = interface_client
    heartbeat = {
        "device_id": "DAS-COLLECTOR-001", "status": "online", "channel_count": 4000,
        "sampling_rate_hz": 1000, "latest_relative_path": "00/Das_data_00-00-49.tdms",
    }
    response = client.post("/api/v1/devices/heartbeats", json=heartbeat)

    assert response.status_code == 202
    current = client.get("/api/v1/devices/DAS-COLLECTOR-001")
    assert current.status_code == 200
    assert current.json()["status"] == "online"
    assert current.json()["received_at"]


@allure.feature("采集端状态接口")
def test_unknown_data_asset_is_a_controlled_404(interface_client):
    client, _ = interface_client
    response = client.post(
        "/api/v1/analysis-jobs",
        json={"data_asset_id": "asset-missing", "start_sample": 0, "n_samples": 100, "channel_start": 1, "channel_end": 2},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "数据资产不存在"


@allure.feature("告警事件接口")
def test_persistent_alarm_is_delivered_to_real_local_http_receiver(interface_client, alarm_receiver):
    client, root = interface_client
    server, receiver = alarm_receiver
    persistent_tdms(root / "persistent.tdms")
    callback = f"http://127.0.0.1:{server.server_port}/alarm-receiver"

    response = client.post("/api/v1/persistence-jobs", json=persistent_payload(alarm_callback_url=callback))

    assert response.status_code == 202
    job = client.get(f"/api/v1/analysis-jobs/{response.json()['job_id']}").json()
    assert job["status"] == "completed"
    events = client.get("/api/v1/alarm-events").json()
    assert events[0]["job_id"] == job["job_id"]
    assert events[0]["deliveries"][0]["status"] == "delivered"
    assert receiver.received[0]["body"]["event_id"] == events[0]["event_id"]
    assert receiver.received[0]["headers"]["X-Event-Id"] == events[0]["event_id"]


@allure.feature("告警事件接口")
def test_alarm_delivery_retries_after_receiver_5xx(interface_client, alarm_receiver):
    client, root = interface_client
    server, receiver = alarm_receiver
    receiver.responses_before_success = 1
    persistent_tdms(root / "persistent.tdms")
    callback = f"http://127.0.0.1:{server.server_port}/alarm-receiver"

    response = client.post("/api/v1/persistence-jobs", json=persistent_payload(alarm_callback_url=callback))

    assert response.status_code == 202
    events = client.get("/api/v1/alarm-events").json()
    assert events[0]["deliveries"][0]["status"] == "delivered"
    assert events[0]["deliveries"][0]["attempts"] == 2
    assert len(receiver.received) == 2
