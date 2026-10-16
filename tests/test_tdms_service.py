from __future__ import annotations

import allure
import numpy as np
import pytest
from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter

from das_monitor.settings import settings
from das_monitor.tdms_service import (
    DataAccessError,
    analyse_file_persistence,
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
                RootObject(properties={"channels": 2, "samples": 200, "sample_ratio": 1000.0}),
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


@allure.feature("DAS 泄漏风险初筛")
def test_normal_channels_do_not_become_risk_candidates(tdms_root):
    rng = np.random.default_rng(7)
    samples_per_channel = 400
    normal_channels = [1.0 + 0.02 * rng.standard_normal(samples_per_channel) for _ in range(8)]
    with TdmsWriter(tdms_root / "normal.tdms") as writer:
        writer.write_segment(
            [
                RootObject(properties={"channels": 8, "samples": samples_per_channel, "sample_ratio": 1000.0}),
                GroupObject("data"),
                ChannelObject("data", "flattened", np.concatenate(normal_channels)),
            ]
        )

    result = analyse_window("normal.tdms", 0, 400, 1, 8, 0.6)

    assert result["candidates"] == []
    assert result["candidate_regions"] == []


@allure.feature("DAS 泄漏风险初筛")
def test_adjacent_abnormal_channels_form_one_spatial_region(tdms_root):
    rng = np.random.default_rng(11)
    samples_per_channel = 500
    time_axis = np.arange(samples_per_channel) / 1000.0
    channels = [1.0 + 0.02 * rng.standard_normal(samples_per_channel) for _ in range(8)]
    for index in (4, 5):
        channels[index] += 0.9 * np.sin(2 * np.pi * 40 * time_axis)
    with TdmsWriter(tdms_root / "region.tdms") as writer:
        writer.write_segment(
            [
                RootObject(properties={"channels": 8, "samples": samples_per_channel, "sample_ratio": 1000.0}),
                GroupObject("data"),
                ChannelObject("data", "flattened", np.concatenate(channels)),
            ]
        )

    result = analyse_window("region.tdms", 0, 500, 1, 8, 0.6)

    assert any(region["start_channel"] == 5 and region["end_channel"] == 6 for region in result["candidate_regions"])


@allure.feature("DAS 分级预警")
def test_repeated_candidate_becomes_persistent_alert(tdms_root):
    rng = np.random.default_rng(42)
    samples_per_channel = 300
    normal_channels = [1.0 + 0.02 * rng.standard_normal(samples_per_channel) for _ in range(5)]
    time_axis = np.arange(samples_per_channel) / 1000.0
    persistent_candidate = 1.0 + 0.02 * rng.standard_normal(samples_per_channel) + 0.8 * np.sin(2 * np.pi * 40 * time_axis)
    with TdmsWriter(tdms_root / "persistent.tdms") as writer:
        writer.write_segment(
            [
                RootObject(properties={"channels": 6, "samples": samples_per_channel, "sample_ratio": 1000.0}),
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


@allure.feature("DAS 分级预警")
def test_single_window_candidate_is_not_persistent_alert(tdms_root):
    rng = np.random.default_rng(13)
    samples_per_channel = 300
    time_axis = np.arange(samples_per_channel) / 1000.0
    channels = [1.0 + 0.02 * rng.standard_normal(samples_per_channel) for _ in range(5)]
    transient = 1.0 + 0.02 * rng.standard_normal(samples_per_channel)
    transient[:100] += 0.8 * np.sin(2 * np.pi * 40 * time_axis[:100])
    channels.append(transient)
    with TdmsWriter(tdms_root / "transient.tdms") as writer:
        writer.write_segment(
            [
                RootObject(properties={"channels": 6, "samples": samples_per_channel, "sample_ratio": 1000.0}),
                GroupObject("data"),
                ChannelObject("data", "flattened", np.concatenate(channels)),
            ]
        )

    result = analyse_persistence(
        "transient.tdms",
        start_sample=0,
        n_samples=100,
        channel_start=1,
        channel_end=6,
        background_fraction=0.6,
        n_windows=3,
        window_step_samples=100,
        min_persistent_windows=2,
    )

    assert result["alerts"] == []
    assert result["transient_candidates"]


@allure.feature("DAS 分级预警")
def test_candidate_repeated_in_sequential_files_becomes_file_alert(tdms_root):
    rng = np.random.default_rng(17)
    samples_per_channel = 300
    time_axis = np.arange(samples_per_channel) / 1000.0
    for file_index in range(3):
        channels = [1.0 + 0.02 * rng.standard_normal(samples_per_channel) for _ in range(5)]
        channels.append(1.0 + 0.02 * rng.standard_normal(samples_per_channel) + 0.8 * np.sin(2 * np.pi * 40 * time_axis))
        with TdmsWriter(tdms_root / f"sequence-{file_index:02d}.tdms") as writer:
            writer.write_segment(
                [
                    RootObject(properties={"channels": 6, "samples": samples_per_channel, "sample_ratio": 1000.0}),
                    GroupObject("data"),
                    ChannelObject("data", "flattened", np.concatenate(channels)),
                ]
            )

    result = analyse_file_persistence(
        "sequence-00.tdms",
        start_sample=0,
        n_samples=300,
        channel_start=1,
        channel_end=6,
        background_fraction=0.6,
        n_files=3,
        min_persistent_files=2,
    )

    assert result["analysis_scope"] == "multi_file_persistence_screening"
    assert result["alerts"][0]["channel"] == 6
    assert result["alerts"][0]["max_consecutive_files"] == 3
