"""Measure a bounded, real TDMS read. Run only against approved local replay data."""

from __future__ import annotations

import argparse
import time

from das_monitor.tdms_service import analyse_window


def main() -> None:
    parser = argparse.ArgumentParser(description="DAS TDMS bounded-window performance smoke test")
    parser.add_argument("--relative-path", default="00/Das_data_00-00-49.tdms")
    parser.add_argument("--samples", type=int, default=10_000)
    parser.add_argument("--channels", type=int, default=24)
    parser.add_argument("--max-seconds", type=float, default=30.0)
    args = parser.parse_args()

    started = time.perf_counter()
    result = analyse_window(
        relative_path=args.relative_path,
        start_sample=0,
        n_samples=args.samples,
        channel_start=1,
        channel_end=args.channels,
        background_fraction=0.60,
    )
    elapsed = time.perf_counter() - started
    print(
        f"PERFORMANCE_RESULT elapsed_seconds={elapsed:.3f} "
        f"channels={args.channels} samples_per_channel={args.samples} "
        f"candidates={result['summary']['candidate_count']}"
    )
    if elapsed > args.max_seconds:
        raise SystemExit(f"超过性能观察阈值 {args.max_seconds:.1f}s")


if __name__ == "__main__":
    main()
