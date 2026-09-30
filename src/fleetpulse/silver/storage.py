"""Silver table storage (local Parquet). Isolated so a Delta store can replace it.

Rebuild semantics: **full, deterministic rebuild from Bronze**. Every build
writes complete new versions of all Silver tables to ``_staging/<build_id>/``
and then publishes them:

1. for each table: current version -> ``_trash/<build_id>/<table>``,
   staged version -> ``<table>`` (directory renames);
2. ``_builds/<build_id>.json`` manifest is written (records the Bronze commit
   ids the build read, counts and configuration);
3. ``_trash/<build_id>`` is deleted.

``recover()`` (start of every build) restores a table from ``_trash`` if a
crash left it missing, then removes leftover staging/trash. So a rebuild never
depends on manually deleting folders, and a rebuild from unchanged Bronze
reproduces the same rows (only ``silver_build_id`` differs).

Delta equivalent: ``replaceWhere``/overwrite of each table in one transaction
per table (or ``CREATE OR REPLACE``), manifest as table properties; later
incremental builds can overwrite only affected ``event_date`` partitions.

Layout / partitioning::

    silver_signal_events/event_date=YYYY-MM-DD/   sorted by pgn, spn, event_timestamp
    silver_telemetry/event_date=YYYY-MM-DD/       sorted by recording_session_id, grid_timestamp
    silver_recording_sessions/                    one small file
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from pyspark.sql import DataFrame

from fleetpulse.silver.schema import RECORDING_SESSIONS, SIGNAL_EVENTS, TELEMETRY

EVENTS = "silver_signal_events"
TELEMETRY_TABLE = "silver_telemetry"
SESSIONS = "silver_recording_sessions"
TABLES = (EVENTS, TELEMETRY_TABLE, SESSIONS)
SCHEMAS = {EVENTS: SIGNAL_EVENTS, TELEMETRY_TABLE: TELEMETRY, SESSIONS: RECORDING_SESSIONS}


class ParquetSilverStore:
    def __init__(self, root: Path, compression: str = "zstd", max_records_per_file: int = 20_000_000):
        self.root = Path(root)
        self.compression = compression
        self.max_records_per_file = max_records_per_file

    def recover(self) -> dict:
        restored = 0
        trash = self.root / "_trash"
        if trash.exists():
            for build in trash.iterdir():
                for table in build.iterdir():
                    if not (self.root / table.name).exists():
                        table.rename(self.root / table.name)
                        restored += 1
            shutil.rmtree(trash)
        shutil.rmtree(self.root / "_staging", ignore_errors=True)
        return {"restored_tables": restored}

    def staged_path(self, build_id: str, table: str) -> Path:
        return self.root / "_staging" / build_id / table

    def write(self, df: DataFrame, build_id: str, table: str) -> None:
        path = str(self.staged_path(build_id, table))
        opts = {"compression": self.compression, "maxRecordsPerFile": self.max_records_per_file}
        if table == SESSIONS:
            df.coalesce(1).write.options(**opts).parquet(path)
            return
        sort = ["pgn", "spn", "event_timestamp", "source_file", "source_line"] if table == EVENTS else [
            "recording_session_id", "grid_timestamp"
        ]
        df.repartition("event_date").sortWithinPartitions(*sort).write.options(**opts).partitionBy(
            "event_date"
        ).parquet(path)

    def read_staged(self, spark, build_id: str, table: str) -> DataFrame:
        return spark.read.schema(SCHEMAS[table]).parquet(str(self.staged_path(build_id, table)))

    def read(self, spark, table: str) -> DataFrame:
        return spark.read.schema(SCHEMAS[table]).parquet(str(self.root / table))

    def publish(self, build_id: str, manifest: dict) -> None:
        trash = self.root / "_trash" / build_id
        trash.mkdir(parents=True, exist_ok=True)
        for table in TABLES:
            current, staged = self.root / table, self.staged_path(build_id, table)
            if current.exists():
                current.rename(trash / table)
            staged.rename(current)
        builds = self.root / "_builds"
        builds.mkdir(exist_ok=True)
        (builds / f"{build_id}.json").write_text(json.dumps(manifest, indent=2, default=str))
        shutil.rmtree(self.root / "_trash", ignore_errors=True)
        shutil.rmtree(self.root / "_staging", ignore_errors=True)


STORAGE_FORMATS = ("parquet", "delta")


def open_silver_store(root: Path, storage_format: str = "parquet"):
    """Silver store for ``storage_format``; both implement the same interface."""
    if storage_format == "parquet":
        return ParquetSilverStore(root)
    if storage_format == "delta":
        from fleetpulse.silver.storage_delta import DeltaSilverStore

        return DeltaSilverStore(root)
    raise ValueError(f"storage_format must be one of {STORAGE_FORMATS}")
