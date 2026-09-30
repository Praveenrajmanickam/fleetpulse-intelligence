"""Controlled local scale experiment: Bronze Delta -> Silver Delta for one workload.

Usage (one workload per process, so every run gets a fresh JVM)::

    python -m fleetpulse.benchmarks.scale_experiment --workload 1m \\
        --members config/scale_1m.txt --out data/scale/1m

Identical configuration for every workload: local[4], 2g driver, 8 shuffle
partitions, Bronze batches of 50 files, Delta storage, 1 s recording gap,
100 ms grid. Nothing is tuned between workloads.

Measured: wall-clock phases (pipeline instrumentation), Spark task metrics
per phase (event log: executor time, CPU, GC, shuffle, spill, peak execution
memory, per-stage task skew), process RSS (JVM + Python), row counts, active
Delta file-size distributions and transaction-log size, recording-session
statistics, and structural correctness checks. Writes ``<out>/results.json``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import time
from pathlib import Path

from pyspark.sql import functions as F

from fleetpulse import delta_support
from fleetpulse.benchmarks import structural_checks
from fleetpulse.benchmarks.probes import MemorySampler, phase_metrics, read_event_log
from fleetpulse.bronze.archive_source import read_member_list
from fleetpulse.bronze.spark import get_spark
from fleetpulse.bronze.storage_delta import DeltaBronzeStore
from fleetpulse.pipelines.bronze_ingest import BronzeIngestion
from fleetpulse.pipelines.silver_build import build_silver
from fleetpulse.silver.storage import EVENTS, SESSIONS, TELEMETRY_TABLE
from fleetpulse.silver.storage_delta import DeltaSilverStore

CONFIG = {"master": "local[4]", "driver_memory": "2g", "shuffle_partitions": 8, "batch_files": 50,
          "storage_format": "delta", "recording_gap_s": 1.0, "grid_period_ms": 100}


def _pct(values: list[int], q: float) -> int:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def file_distribution(spark, path: Path) -> dict:
    """Active files of the current Delta version (not superseded ones)."""
    files = spark.read.format("delta").load(str(path)).inputFiles()
    sizes = [Path(f.replace("file://", "").replace("file:", "")).stat().st_size for f in files]
    log = delta_support.log_stats(path)
    base = {"files": len(sizes), **log, "delta_version": delta_support.version(spark, path)}
    if not sizes:
        return base
    return {**base, "total_mb": round(sum(sizes) / 1e6, 3), "min_kb": round(min(sizes) / 1e3, 1),
            "median_kb": round(statistics.median(sizes) / 1e3, 1), "p90_kb": round(_pct(sizes, 0.9) / 1e3, 1),
            "p95_kb": round(_pct(sizes, 0.95) / 1e3, 1), "max_kb": round(max(sizes) / 1e3, 1)}


def session_stats(spark, silver: DeltaSilverStore) -> dict:
    s = silver.read(spark, SESSIONS)
    t = silver.read(spark, TELEMETRY_TABLE).groupBy("recording_session_id").count().withColumnRenamed("count", "grid_rows")
    e = silver.read(spark, EVENTS).groupBy("recording_session_id").count().withColumnRenamed("count", "events")
    rows = [r.asDict() for r in s.join(t, "recording_session_id", "left").join(e, "recording_session_id", "left")
            .orderBy("session_start").collect()]
    tot = {k: sum((r[k] or 0) for r in rows) for k in ("frame_count", "grid_rows", "events", "duration_s")}
    largest = max(rows, key=lambda r: r["frame_count"])
    return {
        "sessions": len(rows),
        "per_session": [
            {"id": r["recording_session_id"][-22:], "start": str(r["session_start"]), "duration_s": round(r["duration_s"], 1),
             "frames": r["frame_count"], "events": r["events"] or 0, "grid_rows": r["grid_rows"] or 0}
            for r in rows
        ],
        "largest": {
            "id": largest["recording_session_id"][-22:], "duration_s": round(largest["duration_s"], 1),
            "frames": largest["frame_count"],
            "share_of_frames": round(largest["frame_count"] / tot["frame_count"], 3),
            "share_of_grid_rows": round((largest["grid_rows"] or 0) / tot["grid_rows"], 3) if tot["grid_rows"] else None,
            "share_of_duration": round(largest["duration_s"] / tot["duration_s"], 3),
        },
        "frames_per_session": {"min": min(r["frame_count"] for r in rows), "median": statistics.median(r["frame_count"] for r in rows),
                               "max": largest["frame_count"]},
        "duration_s_per_session": {"min": round(min(r["duration_s"] for r in rows), 1),
                                   "median": round(statistics.median(r["duration_s"] for r in rows), 1),
                                   "max": round(max(r["duration_s"] for r in rows), 1)},
    }


def run(workload: str, members_file: Path, archive: Path, out: Path) -> dict:
    members = read_member_list(members_file)
    out = out.resolve()
    event_dir = out / "eventlog"
    event_dir.mkdir(parents=True)
    t0 = time.time()
    spark = get_spark(
        app=f"scale-{workload}", master=CONFIG["master"], driver_memory=CONFIG["driver_memory"],
        shuffle_partitions=CONFIG["shuffle_partitions"],
        extra_conf={"spark.eventLog.enabled": "true", "spark.eventLog.dir": event_dir.as_uri(),
                    "spark.eventLog.compress": "false"},
    )
    session_start_s = time.time() - t0
    sampler = MemorySampler().start()
    bronze_store = DeltaBronzeStore(out / "bronze")
    silver_store = DeltaSilverStore(out / "silver")
    try:
        tb = time.time()
        bronze = BronzeIngestion(spark, bronze_store, batch_files=CONFIG["batch_files"]).run(archive, members)
        bronze_wall = time.time() - tb
        ts = time.time()
        silver = build_silver(spark, out / "bronze", out / "silver", storage_format="delta",
                              recording_gap_s=CONFIG["recording_gap_s"], period_ms=CONFIG["grid_period_ms"])
        silver_wall = time.time() - ts
        tc = time.time()
        frames = bronze_store.read_frames(spark)
        checks = {
            "bronze": structural_checks.bronze_checks(frames, bronze_store.read_log(spark), bronze_store.read_quarantine(spark)),
            "silver": structural_checks.silver_checks(frames, silver_store.read(spark, EVENTS),
                                                      silver_store.read(spark, SESSIONS), silver_store.read(spark, TELEMETRY_TABLE)),
        }
        checks_wall = time.time() - tc
        ts_range = frames.agg(F.min("event_timestamp").alias("a"), F.max("event_timestamp").alias("b")).collect()[0]
        src = bronze_store.read_log(spark).where("status = 'ingested'").agg(
            F.count(F.lit(1)).alias("files"), F.sum("source_size_bytes").alias("bytes")).collect()[0]
        storage = {
            "bronze_can_frames": file_distribution(spark, bronze_store.frames_dir),
            "bronze_quarantine": file_distribution(spark, bronze_store.quarantine_dir),
            "bronze_ingestion_log": file_distribution(spark, bronze_store.log_dir),
            **{t: file_distribution(spark, out / "silver" / t) for t in (EVENTS, SESSIONS, TELEMETRY_TABLE)},
        }
        sessions = session_stats(spark, silver_store)
    finally:
        sampler.stop()
        spark.stop()

    windows = {**bronze["phase_windows"], **silver["phase_windows"]}
    tasks, stage_names = read_event_log(event_dir)
    result = {
        "workload": workload,
        "config": CONFIG,
        "source": {"members": len(members), "first_member": members[0], "last_member": members[-1],
                   "files_ingested": src.files, "raw_csv_bytes": int(src.bytes),
                   "first_event": str(ts_range.a), "last_event": str(ts_range.b)},
        "rows": {"bronze_records": bronze["records"], "bronze_valid": bronze["valid_records"],
                 "bronze_quarantined": bronze["invalid_records"], "signal_events": silver["signal_events"],
                 "telemetry_rows": silver["telemetry_rows"], "recording_sessions": len(silver["recording_sessions"])},
        "wall_s": {"spark_session_start": round(session_start_s, 1), "bronze_total": round(bronze_wall, 1),
                   "silver_total": round(silver_wall, 1), "structural_checks": round(checks_wall, 1)},
        "bronze_timings": bronze["timings"],
        "bronze_commits": bronze["commits"],
        "silver_timings": silver["timings"],
        "spark_phase_metrics": phase_metrics(tasks, stage_names, windows),
        "memory_peaks": sampler.peaks(windows),
        "storage": storage,
        "sessions": sessions,
        "checks": checks,
    }
    (out / "results.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workload", required=True)
    parser.add_argument("--members", type=Path, required=True)
    parser.add_argument("--archive", type=Path, default=Path("data/raw/part_3.tar.xz"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true", help="delete an existing output directory first")
    args = parser.parse_args(argv)
    if args.out.exists():
        if not args.overwrite:
            parser.error(f"{args.out} exists (use --overwrite)")
        shutil.rmtree(args.out)
    r = run(args.workload, args.members, args.archive, args.out)
    print(json.dumps({k: r[k] for k in ("workload", "source", "rows", "wall_s", "bronze_timings", "silver_timings",
                                         "memory_peaks", "checks")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
