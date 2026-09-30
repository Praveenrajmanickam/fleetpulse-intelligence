"""Structural correctness checks for Bronze/Silver outputs at any scale (Spark, no reference oracle)."""

from __future__ import annotations

from functools import reduce

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from fleetpulse.bronze.schema import PAYLOAD_COLUMNS
from fleetpulse.j1939.definitions import MESSAGES
from fleetpulse.silver.schema import FRESH
from fleetpulse.telemetry.alignment import max_age_ms


def _us(c):
    return F.unix_micros(F.col(c).cast("timestamp"))


def bronze_checks(frames: DataFrame, log: DataFrame, quarantine: DataFrame) -> dict:
    n = frames.count()
    distinct = frames.select("source_file", "source_line").distinct().count()
    byte_ok = reduce(
        lambda a, b: a & b,
        [
            F.when(F.lit(i) <= F.col("dlc"), F.col(c).isNotNull() & F.col(c).between(0, 255)).otherwise(F.col(c).isNull())
            for i, c in enumerate(PAYLOAD_COLUMNS, start=1)
        ],
    )
    bad_payload = frames.where(~(byte_ok & F.col("dlc").between(0, 8))).count()
    lineage_nulls = frames.where(
        F.col("source_archive").isNull() | F.col("source_file").isNull() | F.col("source_line").isNull()
        | F.col("ingestion_run_id").isNull()
    ).count()
    ingested = log.where(F.col("status") == "ingested").agg(
        F.sum("record_count").alias("records"), F.sum("valid_record_count").alias("valid"),
        F.sum("invalid_record_count").alias("invalid"),
    ).collect()[0]
    q = quarantine.count()
    return {
        "frames": n,
        "unique_source_line": distinct == n,
        "bad_dlc_or_payload_rows": bad_payload,
        "lineage_null_rows": lineage_nulls,
        "log_records": int(ingested.records or 0),
        "log_valid_equals_frames": int(ingested.valid or 0) == n,
        "log_invalid_equals_quarantine": int(ingested.invalid or 0) == q,
        "reconciles": int(ingested.records or 0) == n + q,
    }


def silver_checks(frames: DataFrame, events: DataFrame, sessions: DataFrame, tele: DataFrame) -> dict:
    key = ["source_file", "source_line", "ingestion_run_id"]
    untraced = events.select(*key, "event_timestamp", "can_id").join(
        frames.select(*key, F.col("event_timestamp").alias("b_ts"), F.col("can_id").alias("b_can")), key, "left"
    ).where(F.col("b_ts").isNull() | (F.col("b_ts") != F.col("event_timestamp")) | (F.col("b_can") != F.col("can_id"))).count()

    s = sessions.select("recording_session_id", "session_start", "session_end", "frame_count")
    ev_outside = events.join(F.broadcast(s), "recording_session_id").where(
        (F.col("event_timestamp") < F.col("session_start")) | (F.col("event_timestamp") > F.col("session_end"))
    ).count()
    ordered = s.orderBy("session_start").collect()
    overlapping = sum(1 for a, b in zip(ordered, ordered[1:]) if b.session_start <= a.session_end)
    frames_in_sessions = sum(r.frame_count for r in ordered)

    t = tele.join(F.broadcast(s), "recording_session_id")
    g = _us("grid_timestamp")
    off_grid = t.where(g % 100_000 != 0).count()
    outside = t.where((g < _us("session_start")) | (g > _us("session_end"))).count()
    expected_rows = s.select(
        "recording_session_id",
        (F.floor(_us("session_end") / 100_000) - F.ceil(_us("session_start") / 100_000) + 1).alias("expected"),
    ).withColumn("expected", F.greatest(F.col("expected"), F.lit(0)))
    got_rows = tele.groupBy("recording_session_id").count()
    grid_row_mismatch = expected_rows.join(got_rows, "recording_session_id", "left").where(
        F.coalesce(F.col("count"), F.lit(0)) != F.col("expected")
    ).count()
    duplicate_grid = tele.groupBy("recording_session_id", "grid_timestamp").count().where("count > 1").count()

    fresh_violations = {}
    for m in MESSAGES:
        age, fr = F.col(f"{m.key}_age_ms"), F.col(f"{m.key}_freshness")
        values_when_not_fresh = reduce(lambda a, b: a | b, [F.col(x.name).isNotNull() for x in m.signals])
        fresh_violations[m.key] = t.where(
            ((fr == FRESH) & (age.isNull() | (age < 0) | (age > max_age_ms(m))))
            | ((fr != FRESH) & (age.isNotNull() | values_when_not_fresh))
        ).count()
    return {
        "events": events.count(),
        "events_not_traceable_to_bronze": untraced,
        "events_outside_their_session": ev_outside,
        "overlapping_sessions": overlapping,
        "session_frames_equal_bronze_frames": frames_in_sessions == frames.count(),
        "telemetry_rows": tele.count(),
        "telemetry_off_100ms_grid": off_grid,
        "telemetry_outside_session": outside,
        "sessions_with_wrong_grid_row_count": grid_row_mismatch,
        "duplicate_grid_rows": duplicate_grid,
        "freshness_contract_violations": fresh_violations,
    }
