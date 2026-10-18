from __future__ import annotations

import hashlib
from itertools import pairwise
from pathlib import Path, PurePosixPath

import numpy as np
from nptdms import TdmsFile
from scipy.signal import coherence as magnitude_squared_coherence

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


def build_data_asset_manifest(relative_path: str, integrity_mode: str = "metadata") -> dict[str, object]:
    """Validate a TDMS input and create a bounded, reproducible intake manifest.

    ``metadata`` is the default because hashing a multi-GB historical replay file for
    every UI click is wasteful. ``sha256`` is an explicit acceptance/archival check.
    """
    path = resolve_tdms_path(relative_path)
    stat = path.stat()
    integrity: dict[str, object] = {
        "mode": integrity_mode,
        "bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
    }
    if integrity_mode == "sha256":
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        integrity["sha256"] = digest.hexdigest()
    return {"metadata": read_metadata(relative_path), "integrity": integrity}


def _robust_z(values: np.ndarray, minimum_scale: float = 1e-12) -> np.ndarray:
    center = np.median(values)
    scale = max(1.4826 * np.median(np.abs(values - center)), minimum_scale)
    return (values - center) / scale


def _expand_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    expanded = mask.copy()
    for index in np.flatnonzero(mask):
        expanded[max(0, index - radius) : min(mask.size, index + radius + 1)] = True
    return expanded


def _band_log_spectrum(signal: np.ndarray, sample_rate_hz: float, low_hz: float, high_hz: float) -> np.ndarray:
    centered = signal - np.mean(signal)
    power = np.square(np.abs(np.fft.rfft(centered * np.hanning(signal.size))))
    frequencies = np.fft.rfftfreq(signal.size, d=1 / sample_rate_hz)
    band = (frequencies >= low_hz) & (frequencies <= high_hz)
    if not np.any(band):
        raise DataAccessError("采样窗口不足以计算指定频段的频谱特征")
    return np.log10(power[band] + np.finfo(np.float64).eps)


def _dominant_frequency(signal: np.ndarray, sample_rate_hz: float, low_hz: float, high_hz: float) -> float:
    centered = signal - np.mean(signal)
    power = np.square(np.abs(np.fft.rfft(centered * np.hanning(signal.size))))
    frequencies = np.fft.rfftfreq(signal.size, d=1 / sample_rate_hz)
    band = (frequencies >= low_hz) & (frequencies <= high_hz)
    if not np.any(band) or np.all(power[band] == 0):
        return 0.0
    return float(frequencies[band][np.argmax(power[band])])


def _local_rms_ratio(rms_values: np.ndarray, radius: int) -> np.ndarray:
    ratios = np.ones_like(rms_values)
    for index, rms in enumerate(rms_values):
        positions = [position for position in range(max(0, index - radius), min(rms_values.size, index + radius + 1)) if position != index]
        baseline = np.median(rms_values[positions]) if positions else np.median(rms_values)
        ratios[index] = rms / max(baseline, np.finfo(np.float64).eps)
    return ratios


def _neighbor_coherence(
    signals: np.ndarray,
    sample_rate_hz: float,
    low_hz: float,
    high_hz: float,
) -> np.ndarray:
    """Return mean magnitude-squared coherence against immediate spatial neighbours."""
    coherence = np.zeros(signals.shape[0], dtype=np.float64)
    for index, signal in enumerate(signals):
        neighbours = [signals[position] for position in (index - 1, index + 1) if 0 <= position < signals.shape[0]]
        neighbour_scores: list[float] = []
        for neighbour in neighbours:
            if np.std(signal) <= np.finfo(np.float64).eps or np.std(neighbour) <= np.finfo(np.float64).eps:
                continue
            frequencies, values = magnitude_squared_coherence(
                signal,
                neighbour,
                fs=sample_rate_hz,
                nperseg=min(256, signal.size),
            )
            band = (frequencies >= low_hz) & (frequencies <= high_hz)
            if np.any(band) and np.all(np.isfinite(values[band])):
                neighbour_scores.append(float(np.mean(values[band])))
        coherence[index] = float(np.mean(neighbour_scores)) if neighbour_scores else 0.0
    return coherence


def _contiguous_regions(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    if not candidates:
        return []
    rank = {"yellow": 1, "orange": 2, "red": 3}
    ordered = sorted(candidates, key=lambda item: int(item["channel"]))
    groups: list[list[dict[str, object]]] = [[ordered[0]]]
    for candidate in ordered[1:]:
        if int(candidate["channel"]) == int(groups[-1][-1]["channel"]) + 1:
            groups[-1].append(candidate)
        else:
            groups.append([candidate])
    regions: list[dict[str, object]] = []
    for group in groups:
        representative = max(group, key=lambda item: float(item["risk_score"]))
        regions.append(
            {
                "start_channel": int(group[0]["channel"]),
                "end_channel": int(group[-1]["channel"]),
                "level": max((str(item["level"]) for item in group), key=lambda level: rank[level]),
                "representative_channel": int(representative["channel"]),
                "max_risk_score": float(representative["risk_score"]),
            }
        )
    return regions


def _sequential_files(relative_path: str, n_files: int) -> list[str]:
    """Select lexicographically consecutive TDMS files from the same directory.

    The naming convention must reflect acquisition order.  A production system should
    use immutable acquisition timestamps rather than infer order from a filename.
    """
    selected_path = resolve_tdms_path(relative_path)
    root = settings.data_root
    same_directory = sorted(selected_path.parent.glob("*.tdms"))
    relative_files = [item.relative_to(root).as_posix() for item in same_directory]
    try:
        start_index = relative_files.index(relative_path)
    except ValueError as exc:  # Defensive: resolve_tdms_path already established membership.
        raise DataAccessError("起始 TDMS 文件不在可用序列中") from exc
    sequence = relative_files[start_index : start_index + n_files]
    if len(sequence) != n_files:
        raise DataAccessError(f"起始文件之后不足 {n_files} 个同目录 TDMS 文件")
    return sequence


def _aggregate_persistence_evidence(
    channel_occurrences: dict[int, list[dict[str, float | int | str]]],
    min_persistent_units: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Turn repeated candidate evidence into alerts and retained transient evidence."""
    severity_rank = {"normal": 0, "yellow": 1, "orange": 2, "red": 3}
    alerts: list[dict[str, object]] = []
    transient_candidates: list[dict[str, object]] = []
    for channel, evidence in sorted(channel_occurrences.items()):
        levels = [str(item["level"]) for item in evidence]
        highest_level = max(levels, key=lambda level: severity_rank[level])
        sequence_indices = [int(item["sequence_index"]) for item in evidence]
        max_consecutive = 1
        current_consecutive = 1
        for previous, current in pairwise(sequence_indices):
            current_consecutive = current_consecutive + 1 if current == previous + 1 else 1
            max_consecutive = max(max_consecutive, current_consecutive)
        frequencies = np.asarray([float(item["dominant_frequency_hz"]) for item in evidence])
        frequency_std = float(np.std(frequencies))
        frequency_stable = bool(frequency_std <= 20.0)
        record: dict[str, float | int | str | bool] = {
            "channel": channel,
            "occurrences": len(levels),
            "max_consecutive_units": max_consecutive,
            "highest_single_unit_level": highest_level,
            "median_differential_rms": float(np.median([float(item["differential_rms"]) for item in evidence])),
            "mean_neighbor_coherence": float(np.mean([float(item["neighbor_coherence"]) for item in evidence])),
            "dominant_frequency_hz_mean": float(np.mean(frequencies)),
            "dominant_frequency_hz_std": frequency_std,
            "frequency_stable": frequency_stable,
        }
        if len(levels) >= min_persistent_units and max_consecutive >= min_persistent_units:
            record["level"] = highest_level if frequency_stable else "yellow"
            alerts.append(record)
        else:
            transient_candidates.append(record)
    return alerts, transient_candidates


def analyse_window(
    relative_path: str,
    start_sample: int,
    n_samples: int,
    channel_start: int,
    channel_end: int,
    background_fraction: float,
    local_radius: int = 2,
    rms_z_threshold: float = 2.5,
    local_ratio_threshold: float = 1.10,
    spectral_z_threshold: float = 2.0,
    guard_channels: int = 2,
    background_iterations: int = 3,
    frequency_low_hz: float = 2.0,
    frequency_high_hz: float = 120.0,
) -> dict[str, object]:
    """Bounded DAS leakage-risk screening based on learned background and multiple signal features."""
    if n_samples > settings.max_samples_per_job:
        raise DataAccessError(f"单任务最多读取 {settings.max_samples_per_job} 个采样点")
    if channel_end < channel_start:
        raise DataAccessError("channel_end 不能小于 channel_start")
    if not 0 < background_fraction < 1:
        raise DataAccessError("background_fraction 必须在 0 和 1 之间")
    if frequency_high_hz <= frequency_low_hz:
        raise DataAccessError("frequency_high_hz 必须大于 frequency_low_hz")

    path = resolve_tdms_path(relative_path)
    with TdmsFile.open(path) as tdms_file:
        channels = [channel for group in tdms_file.groups() for channel in group.channels()]
        logical_channel_count = int(tdms_file.properties.get("channels", len(channels)))
        samples_per_channel = int(tdms_file.properties.get("samples", 0))
        sample_rate_hz = float(tdms_file.properties.get("sample_ratio", 1.0))
        if channel_end > logical_channel_count:
            raise DataAccessError(f"请求通道超过文件上限 {logical_channel_count}")
        flattened_layout = len(channels) == 1 and logical_channel_count > 1 and samples_per_channel > 0
        if flattened_layout and start_sample + n_samples > samples_per_channel:
            raise DataAccessError(f"采样窗口超过单通道长度 {samples_per_channel}")

        signals: list[np.ndarray] = []
        for channel_index in range(channel_start, channel_end + 1):
            if flattened_layout:
                offset = (channel_index - 1) * samples_per_channel + start_sample
                signal = np.asarray(channels[0][offset : offset + n_samples], dtype=np.float64)
            else:
                signal = np.asarray(channels[channel_index - 1][start_sample : start_sample + n_samples], dtype=np.float64)
            if signal.size != n_samples:
                raise DataAccessError("起始采样点超出该 TDMS 通道长度")
            signals.append(signal)

    signal_matrix = np.vstack(signals)
    rms_values = np.sqrt(np.mean(np.square(signal_matrix), axis=1))
    peak_values = np.max(np.abs(signal_matrix), axis=1)
    local_ratios = _local_rms_ratio(rms_values, local_radius)
    rms_z = _robust_z(rms_values)
    spectra = np.vstack([_band_log_spectrum(signal, sample_rate_hz, frequency_low_hz, frequency_high_hz) for signal in signal_matrix])
    coherence = _neighbor_coherence(
        signal_matrix,
        sample_rate_hz,
        frequency_low_hz,
        frequency_high_hz,
    )

    desired_background_count = max(min(8, signal_matrix.shape[0]), round(background_fraction * signal_matrix.shape[0]))
    initial_score = np.abs(rms_z) + np.abs(np.log(np.maximum(local_ratios, np.finfo(np.float64).eps)))
    background_mask = np.zeros(signal_matrix.shape[0], dtype=bool)
    background_mask[np.argsort(initial_score)[:desired_background_count]] = True

    for _ in range(background_iterations):
        profile = np.median(spectra[background_mask], axis=0)
        profile_mad = np.maximum(1.4826 * np.median(np.abs(spectra[background_mask] - profile), axis=0), 0.03)
        spectral_distance = np.sqrt(np.mean(np.square((spectra - profile) / profile_mad), axis=1))
        sound_z_all = (spectral_distance - np.median(spectral_distance[background_mask])) / max(
            1.4826 * np.median(np.abs(spectral_distance[background_mask] - np.median(spectral_distance[background_mask]))), 0.03
        )
        provisional = (rms_z >= rms_z_threshold) & (local_ratios >= local_ratio_threshold) & (sound_z_all >= spectral_z_threshold)
        eligible = ~_expand_mask(provisional, guard_channels)
        if np.count_nonzero(eligible) < min(3, signal_matrix.shape[0]):
            break
        quiet_score = (
            np.maximum(rms_z / rms_z_threshold, 0)
            + np.maximum((local_ratios - 1) / max(local_ratio_threshold - 1, 0.01), 0)
            + np.maximum(sound_z_all / spectral_z_threshold, 0)
        )
        selected = np.flatnonzero(eligible)[np.argsort(quiet_score[eligible])[:desired_background_count]]
        next_mask = np.zeros(signal_matrix.shape[0], dtype=bool)
        next_mask[selected] = True
        if np.array_equal(next_mask, background_mask):
            break
        background_mask = next_mask

    profile = np.median(spectra[background_mask], axis=0)
    profile_mad = np.maximum(1.4826 * np.median(np.abs(spectra[background_mask] - profile), axis=0), 0.03)
    spectral_distance = np.sqrt(np.mean(np.square((spectra - profile) / profile_mad), axis=1))
    background_distances = spectral_distance[background_mask]
    spectral_z = (spectral_distance - np.median(background_distances)) / max(
        1.4826 * np.median(np.abs(background_distances - np.median(background_distances))), 0.03
    )
    candidate_mask = (rms_z >= rms_z_threshold) & (local_ratios >= local_ratio_threshold) & (spectral_z >= spectral_z_threshold)

    metrics: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    for offset, channel_index in enumerate(range(channel_start, channel_end + 1)):
        risk_score = float(rms_z[offset] / rms_z_threshold + local_ratios[offset] / local_ratio_threshold + spectral_z[offset] / spectral_z_threshold)
        level = "normal"
        if candidate_mask[offset]:
            if rms_z[offset] >= 2 * rms_z_threshold and spectral_z[offset] >= 2 * spectral_z_threshold:
                level = "red"
            elif rms_z[offset] >= 1.5 * rms_z_threshold and spectral_z[offset] >= 1.5 * spectral_z_threshold:
                level = "orange"
            else:
                level = "yellow"
        item: dict[str, object] = {
            "channel": channel_index,
            "rms": float(rms_values[offset]),
            "peak_abs": float(peak_values[offset]),
            "dominant_frequency_hz": _dominant_frequency(signal_matrix[offset], sample_rate_hz, frequency_low_hz, frequency_high_hz),
            "local_rms_ratio": float(local_ratios[offset]),
            "rms_z": float(rms_z[offset]),
            "spectral_z": float(spectral_z[offset]),
            "neighbor_coherence": float(coherence[offset]),
            "is_background": bool(background_mask[offset]),
            "risk_score": risk_score,
            "level": level,
            "n_samples": n_samples,
        }
        metrics.append(item)
        if level != "normal":
            candidates.append({key: item[key] for key in ("channel", "level", "risk_score", "rms_z", "local_rms_ratio", "spectral_z", "neighbor_coherence")})

    return {
        "analysis_scope": "single_file_leakage_risk_screening",
        "relative_path": relative_path,
        "window": {"start_sample": start_sample, "requested_n_samples": n_samples, "channel_start": channel_start, "channel_end": channel_end, "channel_count": len(metrics)},
        "detection_config": {"frequency_band_hz": [frequency_low_hz, frequency_high_hz], "rms_z_threshold": rms_z_threshold, "local_ratio_threshold": local_ratio_threshold, "spectral_z_threshold": spectral_z_threshold, "guard_channels": guard_channels, "background_iterations": background_iterations},
        "background": {"method": "iterative RMS/local-ratio/spectral median-MAD background learning", "fraction": background_fraction, "stable_channels": [int(channel_start + index) for index in np.flatnonzero(background_mask)]},
        "summary": {"median_rms": float(np.median(rms_values)), "mad_rms": float(np.median(np.abs(rms_values - np.median(rms_values)))), "candidate_count": len(candidates), "candidate_region_count": len(_contiguous_regions(candidates))},
        "channel_metrics": metrics,
        "candidates": candidates,
        "candidate_regions": _contiguous_regions(candidates),
        "limitation": "结果是历史数据的泄漏风险初筛；必须结合跨文件持续性、空间一致性和现场复核，不能直接作为泄漏定论。",
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
    local_radius: int = 2,
    rms_z_threshold: float = 2.5,
    local_ratio_threshold: float = 1.10,
    spectral_z_threshold: float = 2.0,
    guard_channels: int = 2,
    background_iterations: int = 3,
    frequency_low_hz: float = 2.0,
    frequency_high_hz: float = 120.0,
) -> dict[str, object]:
    """Require a channel to recur across windows before emitting a graded alert."""
    if min_persistent_windows > n_windows:
        raise DataAccessError("min_persistent_windows 不能大于 n_windows")
    step = window_step_samples or n_samples
    windows: list[dict[str, object]] = []
    channel_occurrences: dict[int, list[dict[str, float | int | str]]] = {}

    for window_index in range(n_windows):
        current_start = start_sample + window_index * step
        result = analyse_window(
            relative_path=relative_path,
            start_sample=current_start,
            n_samples=n_samples,
            channel_start=channel_start,
            channel_end=channel_end,
            background_fraction=background_fraction,
            local_radius=local_radius,
            rms_z_threshold=rms_z_threshold,
            local_ratio_threshold=local_ratio_threshold,
            spectral_z_threshold=spectral_z_threshold,
            guard_channels=guard_channels,
            background_iterations=background_iterations,
            frequency_low_hz=frequency_low_hz,
            frequency_high_hz=frequency_high_hz,
        )
        candidates = result["candidates"]
        metric_by_channel = {int(item["channel"]): item for item in result["channel_metrics"]}
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
            metric = metric_by_channel[channel]
            channel_occurrences.setdefault(channel, []).append(
                {
                    "sequence_index": window_index + 1,
                    "level": str(candidate["level"]),
                    "differential_rms": float(metric["rms"]) - float(result["summary"]["median_rms"]),
                    "dominant_frequency_hz": float(metric["dominant_frequency_hz"]),
                    "neighbor_coherence": float(metric["neighbor_coherence"]),
                }
            )

    alerts, transient_candidates = _aggregate_persistence_evidence(
        channel_occurrences,
        min_persistent_windows,
    )
    for record in [*alerts, *transient_candidates]:
        record["max_consecutive_windows"] = record.pop("max_consecutive_units")
        record["highest_single_window_level"] = record.pop("highest_single_unit_level")

    return {
        "analysis_scope": "multi_window_persistence_screening",
        "relative_path": relative_path,
        "rule": {
            "n_windows": n_windows,
            "window_step_samples": step,
            "min_persistent_windows": min_persistent_windows,
            "meaning": "同一逻辑通道在连续窗口中重复成为候选，且主频稳定时才输出分级预警。",
        },
        "windows": windows,
        "alerts": alerts,
        "transient_candidates": transient_candidates,
        "limitation": "仍为离线初筛；未包含跨文件时间轴、空间邻域一致性或现场确认。",
    }


def analyse_file_persistence(
    relative_path: str,
    start_sample: int,
    n_samples: int,
    channel_start: int,
    channel_end: int,
    background_fraction: float,
    n_files: int,
    min_persistent_files: int,
    local_radius: int = 2,
    rms_z_threshold: float = 2.5,
    local_ratio_threshold: float = 1.10,
    spectral_z_threshold: float = 2.0,
    guard_channels: int = 2,
    background_iterations: int = 3,
    frequency_low_hz: float = 2.0,
    frequency_high_hz: float = 120.0,
) -> dict[str, object]:
    """Require a repeated candidate in consecutive historical TDMS files.

    This is the offline approximation of the original application's cross-file
    persistence rule.  The source files are opened read-only and processed one at
    a time, so a long replay does not load complete TDMS files into memory.
    """
    if min_persistent_files > n_files:
        raise DataAccessError("min_persistent_files 不能大于 n_files")
    relative_paths = _sequential_files(relative_path, n_files)
    file_results: list[dict[str, object]] = []
    channel_occurrences: dict[int, list[dict[str, float | int | str]]] = {}
    for file_index, current_path in enumerate(relative_paths, start=1):
        result = analyse_window(
            relative_path=current_path,
            start_sample=start_sample,
            n_samples=n_samples,
            channel_start=channel_start,
            channel_end=channel_end,
            background_fraction=background_fraction,
            local_radius=local_radius,
            rms_z_threshold=rms_z_threshold,
            local_ratio_threshold=local_ratio_threshold,
            spectral_z_threshold=spectral_z_threshold,
            guard_channels=guard_channels,
            background_iterations=background_iterations,
            frequency_low_hz=frequency_low_hz,
            frequency_high_hz=frequency_high_hz,
        )
        candidates = result["candidates"]
        metric_by_channel = {int(item["channel"]): item for item in result["channel_metrics"]}
        file_results.append(
            {
                "file_index": file_index,
                "relative_path": current_path,
                "candidate_count": len(candidates),
                "candidate_channels": [item["channel"] for item in candidates],
                "candidate_regions": result["candidate_regions"],
            }
        )
        for candidate in candidates:
            channel = int(candidate["channel"])
            metric = metric_by_channel[channel]
            channel_occurrences.setdefault(channel, []).append(
                {
                    "sequence_index": file_index,
                    "level": str(candidate["level"]),
                    "differential_rms": float(metric["rms"]) - float(result["summary"]["median_rms"]),
                    "dominant_frequency_hz": float(metric["dominant_frequency_hz"]),
                    "neighbor_coherence": float(metric["neighbor_coherence"]),
                }
            )

    alerts, transient_candidates = _aggregate_persistence_evidence(
        channel_occurrences,
        min_persistent_files,
    )
    for record in [*alerts, *transient_candidates]:
        record["max_consecutive_files"] = record.pop("max_consecutive_units")
        record["highest_single_file_level"] = record.pop("highest_single_unit_level")
    return {
        "analysis_scope": "multi_file_persistence_screening",
        "relative_paths": relative_paths,
        "rule": {
            "n_files": n_files,
            "min_persistent_files": min_persistent_files,
            "ordering": "同一目录内按文件名词典序选择连续 TDMS 文件；数据接入时应改为设备采集时间排序。",
            "meaning": "同一逻辑通道在连续历史文件中重复成为候选，且主频稳定时才输出分级预警。",
        },
        "files": file_results,
        "alerts": alerts,
        "transient_candidates": transient_candidates,
        "limitation": "这是离线回放的跨文件筛查；现场泄漏结论仍需要空间标定、设备时间戳和现场复核。",
    }
