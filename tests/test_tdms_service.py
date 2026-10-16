from __future__ import annotations

import allure
import numpy as np
import pytest
from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter

from das_monitor.settings import settings
from das_monitor.tdms_service import (
    DataAccessError,
    analyse_persistence,
    analyse_window,
    resolve_tdms_path,
)


@pytest.fixture
def tdms_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_root", tmp_path)
    return tmp_path


def write_flattened_tdms(path):
    normal_channel = np.ones(200, dtype=np.float64)
    elevated_channel = np.full(200, 10.0, dtype=np.float64)
    with TdmsWriter(path) as writer:
        writer.write_segment(
            [
                RootObject(properties={"channels": 2, "samples": 200}),
                GroupObject("data"),
                ChannelObject("data", "flattened", np.concatenate([normal_channel, elevated_channel])),
            ]
        )


@allure.feature("TDMS 数据安全")
def test_rejects_path_traversal(tdms_root):
    with pytest.raises(DataAccessError, match="不含 .."):
        resolve_tdms_path("../private.tdms")


@allure.feature("DAS 离线初筛")
def test_reads_bounded_flattened_channel_window(tdms_root):
    file_path = tdms_root / "demo.tdms"
    write_flattened_tdms(file_path)

    result = analyse_window("demo.tdms", start_sample=0, n_samples=100, channel_start=1, channel_end=2, background_fraction=0.5)

    assert result["window"]["channel_count"] == 2
    assert result["channel_metrics"][0]["n_samples"] == 100
    assert result["channel_metrics"][1]["rms"] == pytest.approx(10.0)


@allure.feature("DAS 分级预警")
def test_repeated_candidate_becomes_persistent_alert(tdms_root):
    rng = np.random.default_rng(42)
    samples_per_channel = 300
    normal_channels = [1.0 + 0.02 * rng.standard_normal(samples_per_channel) for _ in range(5)]
    persistent_candidate = 3.0 + 0.02 * rng.standard_normal(samples_per_channel)
    with TdmsWriter(tdms_root / "persistent.tdms") as writer:
        writer.write_segment(
            [
                RootObject(properties={"channels": 6, "samples": samples_per_channel}),
                GroupObject("data"),
                ChannelObject("data", "flattened", np.concatenate([*normal_channels, persistent_candidate])),
            ]
        )

    result = analyse_persistence(
        "persistent.tdms",
        start_sample=0,
        n_samples=100,
        channel_start=1,
        channel_end=6,
        background_fraction=0.6,
        n_windows=3,
        window_step_samples=100,
        min_persistent_windows=2,
    )

    assert result["alerts"]
    assert result["alerts"][0]["channel"] == 6
    assert result["alerts"][0]["occurrences"] == 3
