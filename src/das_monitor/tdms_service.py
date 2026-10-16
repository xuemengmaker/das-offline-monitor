from __future__ import annotations

from pathlib import Path, PurePosixPath

import numpy as np
from nptdms import TdmsFile

from .settings import settings


class DataAccessError(ValueError):
    """Request points outside the configured read-only TDMS root or is otherwise invalid."""


def resolve_tdms_path(relative_path: str) -> Path:
    candidate = PurePosixPath(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DataAccessError("只允许数据根目录下、不含 .. 的相对路径")
    if candidate.suffix.lower() != ".tdms":
        raise DataAccessError("只允许读取 .tdms 文件")

    root = settings.data_root.resolve()
    resolved = (root / Path(*candidate.parts)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DataAccessError("请求路径不在数据根目录内") from exc
    if not resolved.is_file():
        raise DataAccessError("TDMS 文件不存在")
    return resolved


def list_tdms_files(limit: int = 100) -> list[dict[str, int | str]]:
    root = settings.data_root
    if not root.is_dir():
        raise DataAccessError(f"数据根目录不可用: {root}")
    files: list[dict[str, int | str]] = []
    for file_path in sorted(root.rglob("*.tdms")):
        files.append(
            {
                "relative_path": file_path.relative_to(root).as_posix(),
                "bytes": file_path.stat().st_size,
            }
        )
        if len(files) >= limit:
            break
    return files


def read_metadata(relative_path: str) -> dict[str, object]:
    path = resolve_tdms_path(relative_path)
    with TdmsFile.open(path) as tdms_file:
        groups = tdms_file.groups()
        channels = [channel for group in groups for channel in group.channels()]
        logical_channel_count = int(tdms_file.properties.get("channels", len(channels)))
        samples_per_channel = int(tdms_file.properties.get("samples", len(channels[0]) if channels else 0))
        return {
            "relative_path": relative_path,
            "group_count": len(groups),
            "physical_tdms_channel_count": len(channels),
            "logical_channel_count": logical_channel_count,
            "samples_per_channel": samples_per_channel,
            "storage_layout": "single_flattened_channel" if len(channels) == 1 and logical_channel_count > 1 else "one_tdms_channel_per_logical_channel",
            "channels": [f"{channel.group_name}/{channel.name}" for channel in channels[:10]],
        }


def analyse_window(
    relative_path: str,
    start_sample: int,
    n_samples: int,
    channel_start: int,
    channel_end: int,
    background_fraction: float,
) -> dict[str, object]:
    """Read a bounded TDMS window and perform transparent preliminary screening."""
    if n_samples > settings.max_samples_per_job:
        raise DataAccessError(f"单任务最多读取 {settings.max_samples_per_job} 个采样点")
    if channel_end < channel_start:
        raise DataAccessError("channel_end 不能小于 channel_start")

    path = resolve_tdms_path(relative_path)
    with TdmsFile.open(path) as tdms_file:
        channels = [channel for group in tdms_file.groups() for channel in group.channels()]
        logical_channel_count = int(tdms_file.properties.get("channels", len(channels)))
        samples_per_channel = int(tdms_file.properties.get("samples", 0))
        if channel_end > logical_channel_count:
            raise DataAccessError(f"请求通道超过文件上限 {logical_channel_count}")

        metrics: list[dict[str, float | int]] = []
        flattened_layout = len(channels) == 1 and logical_channel_count > 1 and samples_per_channel > 0
        if flattened_layout and start_sample + n_samples > samples_per_channel:
            raise DataAccessError(f"采样窗口超过单通道长度 {samples_per_channel}")

        for index in range(channel_start, channel_end + 1):
            if flattened_layout:
                # This TDMS stores all logical channels in one physical TDMS channel,
                # in contiguous channel-major blocks. Read just one bounded block per channel.
                offset = (index - 1) * samples_per_channel + start_sample
                signal = np.asarray(channels[0][offset : offset + n_samples], dtype=np.float64)
            else:
                signal = np.asarray(channels[index - 1][start_sample : start_sample + n_samples], dtype=np.float64)
            if signal.size == 0:
                raise DataAccessError("起始采样点超出该 TDMS 通道长度")
            rms = float(np.sqrt(np.mean(np.square(signal))))
            peak_abs = float(np.max(np.abs(signal)))
            sample_rate_hz = float(tdms_file.properties.get("sample_ratio", 1.0))
            centered = signal - np.mean(signal)
            spectrum = np.abs(np.fft.rfft(centered))
            if spectrum.size > 1:
                spectrum[0] = 0.0
                dominant_frequency_hz = float(np.fft.rfftfreq(signal.size, d=1 / sample_rate_hz)[np.argmax(spectrum)])
            else:
                dominant_frequency_hz = 0.0
            metrics.append(
                {
                    "channel": index,
                    "rms": rms,
                    "peak_abs": peak_abs,
                    "dominant_frequency_hz": dominant_frequency_hz,
                    "n_samples": int(signal.size),
                }
            )

    rms_values = np.asarray([item["rms"] for item in metrics], dtype=np.float64)
    median = float(np.median(rms_values))
    mad = float(np.median(np.abs(rms_values - median)))
    robust_scale = 1.4826 * mad
    if robust_scale < np.finfo(np.float64).eps:
        robust_z = np.zeros_like(rms_values)
    else:
        robust_z = (rms_values - median) / robust_scale

    stable_count = max(1, int(np.floor(len(metrics) * background_fraction)))
    stable_indexes = np.argsort(np.abs(robust_z))[:stable_count]
    stable_channels = {metrics[int(index)]["channel"] for index in stable_indexes}
    candidates: list[dict[str, float | int | str]] = []
    for item, z_score in zip(metrics, robust_z, strict=True):
        item["robust_z"] = float(z_score)
        if z_score >= 5:
            level = "red"
        elif z_score >= 3:
            level = "orange"
        elif z_score >= 2:
            level = "yellow"
        else:
            level = "normal"
        item["level"] = level
        if level != "normal":
            candidates.append({"channel": item["channel"], "robust_z": float(z_score), "level": level})

    return {
        "analysis_scope": "single_file_preliminary_screening",
        "relative_path": relative_path,
        "window": {
            "start_sample": start_sample,
            "requested_n_samples": n_samples,
            "channel_start": channel_start,
            "channel_end": channel_end,
            "channel_count": len(metrics),
        },
        "background": {
            "method": "RMS median/MAD robust Z-score",
            "fraction": background_fraction,
            "stable_channels": sorted(stable_channels),
        },
        "summary": {"median_rms": median, "mad_rms": mad, "candidate_count": len(candidates)},
        "channel_metrics": metrics,
        "candidates": candidates,
        "limitation": "单文件窗口初筛，不能替代跨文件持续性判定、现场联调或最终泄漏结论。",
    }


def analyse_persistence(
    relative_path: str,
    start_sample: int,
    n_samples: int,
    channel_start: int,
    channel_end: int,
    background_fraction: float,
    n_windows: int,
    window_step_samples: int | None,
    min_persistent_windows: int,
) -> dict[str, object]:
    """Require a channel to recur across windows before emitting a graded alert."""
    if min_persistent_windows > n_windows:
        raise DataAccessError("min_persistent_windows 不能大于 n_windows")
    step = window_step_samples or n_samples
    windows: list[dict[str, object]] = []
    channel_occurrences: dict[int, list[str]] = {}
    severity_rank = {"normal": 0, "yellow": 1, "orange": 2, "red": 3}

    for window_index in range(n_windows):
        current_start = start_sample + window_index * step
        result = analyse_window(
            relative_path=relative_path,
            start_sample=current_start,
            n_samples=n_samples,
            channel_start=channel_start,
            channel_end=channel_end,
            background_fraction=background_fraction,
        )
        candidates = result["candidates"]
        windows.append(
            {
                "window_index": window_index + 1,
                "start_sample": current_start,
                "candidate_count": len(candidates),
                "candidate_channels": [item["channel"] for item in candidates],
            }
        )
        for candidate in candidates:
            channel = int(candidate["channel"])
            channel_occurrences.setdefault(channel, []).append(str(candidate["level"]))

    alerts: list[dict[str, int | str]] = []
    transient_candidates: list[dict[str, int | str]] = []
    for channel, levels in sorted(channel_occurrences.items()):
        highest_level = max(levels, key=lambda level: severity_rank[level])
        record: dict[str, int | str] = {
            "channel": channel,
            "occurrences": len(levels),
            "highest_single_window_level": highest_level,
        }
        if len(levels) >= min_persistent_windows:
            record["level"] = highest_level
            alerts.append(record)
        else:
            transient_candidates.append(record)

    return {
        "analysis_scope": "multi_window_persistence_screening",
        "relative_path": relative_path,
        "rule": {
            "n_windows": n_windows,
            "window_step_samples": step,
            "min_persistent_windows": min_persistent_windows,
            "meaning": "同一逻辑通道在至少指定数量的窗口中成为候选，才输出分级预警。",
        },
        "windows": windows,
        "alerts": alerts,
        "transient_candidates": transient_candidates,
        "limitation": "仍为离线初筛；未包含跨文件时间轴、空间邻域一致性或现场确认。",
    }
