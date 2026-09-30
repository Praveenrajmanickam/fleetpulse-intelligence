"""Silver build: bronze_can_frames -> silver_signal_events -> silver_telemetry.

Usage::

    python -m fleetpulse.pipelines.silver_build --bronze-root data/bronze --silver-root data/silver

Steps (each reads the previous step's written output):

1. recording sessions over all valid Bronze frames (silver.sessions);
2. decode verified FMS signals into long signal events, attach
   recording_session_id, write (silver.decode);
3. read the written signal events and recording sessions back and align the
   events on a 10 Hz grid spanning each recording session (silver.alignment),
   write telemetry;
4. publish all tables atomically-by-rename with a build manifest.

Reconciliation: events written == decoded frames x signals per message;
every valid Bronze frame belongs to exactly one recording session.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import functions as F

from fleetpulse.bronze.storage import STORAGE_FORMATS, open_bronze_store
from fleetpulse.j1939.definitions import MESSAGES
from fleetpulse.silver import alignment, decode, sessions
from fleetpulse.silver.schema import DATASET_VEHICLE_ID, RECORDING_SESSIONS, SIGNAL_EVENTS
from fleetpulse.silver.storage import EVENTS, SESSIONS, TELEMETRY_TABLE, open_silver_store
from fleetpulse.telemetry.alignment import DEFAULT_PERIOD_MS, max_age_ms
from fleetpulse.validation.continuity import DEFAULT_RECORDING_GAP_S

log = logging.getLogger("fleetpulse.silver")



def new_build_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def build_silver(
    spark,
    bronze_root: Path,
    silver_root: Path,
    vehicle_id: str = DATASET_VEHICLE_ID,
    recording_gap_s: float = DEFAULT_RECORDING_GAP_S,
    period_ms: int = DEFAULT_PERIOD_MS,
    build_id: str | None = None,
    storage_format: str = "parquet",
) -> dict:
    """``storage_format`` applies to both the Bronze input and the Silver output."""
    t0 = time.perf_counter()
    timings = {}
    windows = {}  # phase -> [start_epoch, end_epoch] (instrumentation only)
    build_id = build_id or new_build_id()
    store = open_silver_store(silver_root, storage_format)
    recovered = store.recover()
    bronze = open_bronze_store(bronze_root, storage_format)
    bronze_snapshot = sorted(bronze.committed_commit_ids())
    frames = bronze.read_frames(spark)

    # 1. recording sessions
    t, w = time.perf_counter(), time.time()
    pieces = sessions.file_pieces(frames, recording_gap_s)
    sess, breaks = sessions.assemble_sessions(pieces, recording_gap_s)
    session_rows = sessions.session_rows(sess, vehicle_id, recording_gap_s, build_id)
    sessions_df = spark.createDataFrame(session_rows, RECORDING_SESSIONS)
    n_frames = sum(p["frames"] for p in pieces)
    timings["sessions_s"] = round(time.perf_counter() - t, 2)
    windows["silver_sessions"] = [w, time.time()]
    log.info("%d frames -> %d recording session(s), %d break(s)", n_frames, len(sess), len(breaks))

    # 2. signal events
    t, w = time.perf_counter(), time.time()
    events = sessions.assign_sessions(decode.decode(frames, vehicle_id, build_id), sessions_df)
    store.write(events.select(*SIGNAL_EVENTS.fieldNames()), build_id, EVENTS)
    store.write(sessions_df, build_id, SESSIONS)
    timings["signal_events_write_s"] = round(time.perf_counter() - t, 2)
    windows["silver_events_write"] = [w, time.time()]
    tv, w = time.perf_counter(), time.time()
    events_written = store.read_staged(spark, build_id, EVENTS)
    n_events = events_written.count()
    expected = decode.expected_event_count(frames)
    if n_events != expected:
        raise RuntimeError(f"signal events written {n_events} != expected {expected}")
    excluded = {r.pgn: r["count"] for r in decode.excluded_frames(decode.with_identifier(frames)).collect()}
    timings["signal_events_verify_s"] = round(time.perf_counter() - tv, 2)
    windows["silver_events_verify"] = [w, time.time()]
    timings["signal_events_s"] = round(time.perf_counter() - t, 2)

    # 3. telemetry from the written events
    t, w = time.perf_counter(), time.time()
    sessions_written = store.read_staged(spark, build_id, SESSIONS)
    telemetry = alignment.build(events_written, sessions_written, vehicle_id, build_id, period_ms)
    store.write(telemetry, build_id, TELEMETRY_TABLE)
    timings["telemetry_write_s"] = round(time.perf_counter() - t, 2)
    windows["silver_telemetry_write"] = [w, time.time()]
    tv, w = time.perf_counter(), time.time()
    tele_written = store.read_staged(spark, build_id, TELEMETRY_TABLE)
    n_tele = tele_written.count()
    timings["telemetry_verify_s"] = round(time.perf_counter() - tv, 2)
    windows["silver_telemetry_verify"] = [w, time.time()]
    timings["telemetry_s"] = round(time.perf_counter() - t, 2)

    per_signal = {
        r.signal_name: {"events": r["count"], "valid": r["valid"]}
        for r in events_written.groupBy("signal_name")
        .agg(F.count(F.lit(1)).alias("count"), F.sum((F.col("signal_status") == "valid").cast("long")).alias("valid"))
        .collect()
    }
    per_session = {
        r.recording_session_id: r["count"] for r in tele_written.groupBy("recording_session_id").count().collect()
    }
    manifest = {
        "silver_build_id": build_id,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "storage_format": storage_format,
        "bronze_root": str(bronze_root),
        "bronze_commit_ids": bronze_snapshot,
        "vehicle_id": vehicle_id,
        "vehicle_id_note": "dataset-level identifier, not decoded from CAN",
        "recording_gap_s": recording_gap_s,
        "grid_period_ms": period_ms,
        "max_age_ms": {m.acronym: max_age_ms(m) for m in MESSAGES},
        "recovered": recovered,
        "bronze_frames": n_frames,
        "recording_sessions": [
            {k: r[k] for k in ("recording_session_id", "session_start", "session_end", "duration_s", "frame_count",
                               "gap_before_s", "gap_before_kind")}
            for r in session_rows
        ],
        "breaks": breaks,
        "signal_events": n_events,
        "signal_events_per_signal": per_signal,
        "frames_excluded_dlc_not_8": excluded,
        "telemetry_rows": n_tele,
        "telemetry_rows_per_session": per_session,
        "timings": timings,
        "phase_windows": windows,
    }
    store.publish(build_id, manifest)
    manifest["runtime_s"] = round(time.perf_counter() - t0, 2)
    return manifest


def main(argv: list[str] | None = None) -> int:
    from fleetpulse.bronze.spark import get_spark

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bronze-root", type=Path, default=Path("data/bronze"))
    parser.add_argument("--silver-root", type=Path, default=Path("data/silver"))
    parser.add_argument("--recording-gap-s", type=float, default=DEFAULT_RECORDING_GAP_S)
    parser.add_argument("--period-ms", type=int, default=DEFAULT_PERIOD_MS)
    parser.add_argument("--vehicle-id", default=DATASET_VEHICLE_ID)
    parser.add_argument("--format", choices=STORAGE_FORMATS, default="parquet", help="table storage format")
    parser.add_argument("--master", default="local[4]")
    parser.add_argument("--driver-memory", default="2g")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    spark = get_spark(app="fleetpulse-silver", master=args.master, driver_memory=args.driver_memory)
    try:
        manifest = build_silver(
            spark, args.bronze_root, args.silver_root, args.vehicle_id, args.recording_gap_s, args.period_ms,
            storage_format=args.format,
        )
    finally:
        spark.stop()
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("breaks",)}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
