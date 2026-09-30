"""Compare Delta Lake tables with the validated Parquet baseline, row by row.

Usage::

    python -m fleetpulse.pipelines.delta_parity \\
        --parquet-bronze data/bronze --delta-bronze data/delta/bronze \\
        --parquet-silver data/silver --delta-silver data/delta/silver

For every table: identical schema (column names and types, as read);
identical row counts; ``exceptAll`` in both directions over all contract
columns except per-run identifiers (bag semantics: duplicates count, NULL
equals NULL, doubles compared exactly); identical per-column NULL counts.

Excluded columns - they identify a run, not the data, and legitimately differ
between two independent runs: ``ingested_at`` and ``ingestion_run_id``
(Bronze, and inherited by Silver events), ``silver_build_id`` (Silver).

Lineage check (per storage format): every Silver signal event's
(source_file, source_line, ingestion_run_id) exists in that format's Bronze
frames with the same event_timestamp and can_id.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from fleetpulse.bronze.schema import BRONZE_CAN_FRAMES, BRONZE_QUARANTINE, INGESTION_LOG
from fleetpulse.bronze.storage import open_bronze_store
from fleetpulse.silver.storage import EVENTS, SCHEMAS, SESSIONS, TELEMETRY_TABLE, open_silver_store

RUN_COLUMNS = {"ingested_at", "ingestion_run_id", "silver_build_id"}


def compare(parquet: DataFrame, delta: DataFrame, schema) -> dict:
    cols = [c for c in schema.fieldNames() if c not in RUN_COLUMNS]
    p, d = parquet.select(*cols), delta.select(*cols)
    p_types = [(f.name, f.dataType.simpleString()) for f in parquet.select(*schema.fieldNames()).schema]
    d_types = [(f.name, f.dataType.simpleString()) for f in delta.select(*schema.fieldNames()).schema]
    nulls = lambda df: df.select([F.sum(F.col(c).isNull().cast("long")).alias(c) for c in cols]).collect()[0].asDict()  # noqa: E731
    pn, dn = nulls(p), nulls(d)
    out = {
        "parquet_rows": p.count(),
        "delta_rows": d.count(),
        "only_in_parquet": p.exceptAll(d).count(),
        "only_in_delta": d.exceptAll(p).count(),
        "schema_identical": p_types == d_types,
        "null_counts_identical": pn == dn,
        "columns_compared": len(cols),
    }
    out["identical"] = (
        out["parquet_rows"] == out["delta_rows"]
        and out["only_in_parquet"] == 0
        and out["only_in_delta"] == 0
        and out["schema_identical"]
        and out["null_counts_identical"]
    )
    return out


def lineage(events: DataFrame, frames: DataFrame) -> dict:
    key = ["source_file", "source_line", "ingestion_run_id"]
    f = frames.select(*key, F.col("event_timestamp").alias("f_ts"), F.col("can_id").alias("f_can"))
    j = events.select(*key, "event_timestamp", "can_id").join(f, key, "left")
    return {
        "events": events.count(),
        "missing_bronze_frame": j.where(F.col("f_ts").isNull()).count(),
        "timestamp_or_can_id_mismatch": j.where(
            F.col("f_ts").isNotNull() & ((F.col("f_ts") != F.col("event_timestamp")) | (F.col("f_can") != F.col("can_id")))
        ).count(),
    }


def run(spark, parquet_bronze: Path, delta_bronze: Path, parquet_silver: Path, delta_silver: Path) -> dict:
    pb, db = open_bronze_store(parquet_bronze, "parquet"), open_bronze_store(delta_bronze, "delta")
    ps, ds = open_silver_store(parquet_silver, "parquet"), open_silver_store(delta_silver, "delta")
    ingested = lambda df: df.where(F.col("status") == "ingested").drop("commit_id")  # noqa: E731
    log_schema = type(INGESTION_LOG)([f for f in INGESTION_LOG.fields if f.name not in ("commit_id",)])
    tables = {
        "bronze_can_frames": compare(pb.read_frames(spark), db.read_frames(spark), BRONZE_CAN_FRAMES),
        "bronze_ingestion_log[ingested]": compare(ingested(pb.read_log(spark)), ingested(db.read_log(spark)), log_schema),
        EVENTS: compare(ps.read(spark, EVENTS), ds.read(spark, EVENTS), SCHEMAS[EVENTS]),
        SESSIONS: compare(ps.read(spark, SESSIONS), ds.read(spark, SESSIONS), SCHEMAS[SESSIONS]),
        TELEMETRY_TABLE: compare(ps.read(spark, TELEMETRY_TABLE), ds.read(spark, TELEMETRY_TABLE), SCHEMAS[TELEMETRY_TABLE]),
    }
    if pb.quarantine_dir.exists() or (db.quarantine_dir / "_delta_log").exists():
        pq = pb.read_quarantine(spark) if pb.quarantine_dir.exists() else spark.createDataFrame([], BRONZE_QUARANTINE)
        tables["bronze_quarantine"] = compare(pq, db.read_quarantine(spark), BRONZE_QUARANTINE)
    lin = {
        "parquet": lineage(ps.read(spark, EVENTS), pb.read_frames(spark)),
        "delta": lineage(ds.read(spark, EVENTS), db.read_frames(spark)),
    }
    ok = all(t["identical"] for t in tables.values()) and all(
        v["missing_bronze_frame"] == 0 and v["timestamp_or_can_id_mismatch"] == 0 for v in lin.values()
    )
    return {"parity": ok, "excluded_run_columns": sorted(RUN_COLUMNS), "tables": tables, "lineage": lin}


def main(argv: list[str] | None = None) -> int:
    from fleetpulse.bronze.spark import get_spark

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--parquet-bronze", type=Path, default=Path("data/bronze"))
    parser.add_argument("--delta-bronze", type=Path, default=Path("data/delta/bronze"))
    parser.add_argument("--parquet-silver", type=Path, default=Path("data/silver"))
    parser.add_argument("--delta-silver", type=Path, default=Path("data/delta/silver"))
    parser.add_argument("--out", type=Path, default=Path("data/delta/_parity/delta_vs_parquet.json"))
    args = parser.parse_args(argv)
    spark = get_spark(app="fleetpulse-delta-parity", master="local[4]")
    try:
        report = run(spark, args.parquet_bronze, args.delta_bronze, args.parquet_silver, args.delta_silver)
    finally:
        spark.stop()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["parity"] else 1


if __name__ == "__main__":
    sys.exit(main())
