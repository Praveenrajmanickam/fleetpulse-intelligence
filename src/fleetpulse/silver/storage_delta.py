"""Silver tables stored as Delta Lake tables (same contract as ParquetSilverStore).

Tables (path-based, under ``root``), created up front with explicit schemas::

    silver_signal_events        partitioned by event_date
    silver_telemetry            partitioned by event_date
    silver_recording_sessions   unpartitioned

Rebuild semantics are unchanged: every build is a full, deterministic rebuild
from Bronze. Each table is replaced with one ``mode("overwrite")`` Delta
transaction (schema must match; ``overwriteSchema`` is never set), so readers
of a single table always see a complete old or new version - never a mix.

Cross-table consistency: Delta commits are per table, so the three
overwrites are three commits. The build manifest ``_builds/<build_id>.json``
is written last and records the Delta version of every table in that build.
``read()`` serves the versions pinned by the latest manifest (time travel), so
a build that fails half-way (e.g. events overwritten, telemetry not) is never
observed as a mixed snapshot; the next build supersedes it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from pyspark.sql import DataFrame

from fleetpulse import delta_support
from fleetpulse.silver.storage import EVENTS, SCHEMAS, SESSIONS, TABLES, TELEMETRY_TABLE

PARTITIONS = {EVENTS: ("event_date",), TELEMETRY_TABLE: ("event_date",), SESSIONS: ()}
SORT = {
    EVENTS: ["pgn", "spn", "event_timestamp", "source_file", "source_line"],
    TELEMETRY_TABLE: ["recording_session_id", "grid_timestamp"],
}


class DeltaSilverStore:
    format = "delta"

    def __init__(self, root: Path, compression: str = "zstd", max_records_per_file: int = 20_000_000):
        self.root = Path(root).resolve()  # Delta needs absolute table locations
        self.compression = compression  # same codec as ParquetSilverStore
        self.max_records_per_file = max_records_per_file
        self._written: dict[tuple[str, str], int] = {}  # (build_id, table) -> version

    def path(self, table: str) -> Path:
        return self.root / table

    def recover(self) -> dict:
        shutil.rmtree(self.root / "_staging", ignore_errors=True)
        return {"restored_tables": 0}

    def write(self, df: DataFrame, build_id: str, table: str) -> None:
        spark = df.sparkSession
        delta_support.ensure_table(spark, self.path(table), SCHEMAS[table], PARTITIONS[table])
        df = df.select(*SCHEMAS[table].fieldNames())
        df = df.coalesce(1) if table == SESSIONS else df.repartition("event_date").sortWithinPartitions(*SORT[table])
        (
            df.write.format("delta")
            .mode("overwrite")
            .option("maxRecordsPerFile", self.max_records_per_file)
            .option("compression", self.compression)
            .save(str(self.path(table)))
        )
        self._written[(build_id, table)] = delta_support.version(spark, self.path(table))

    def read_staged(self, spark, build_id: str, table: str) -> DataFrame:
        """The version this build wrote (time travel), independent of later writes."""
        return spark.read.format("delta").option("versionAsOf", self._written[(build_id, table)]).load(
            str(self.path(table))
        )

    def latest_manifest(self) -> dict | None:
        builds = sorted((self.root / "_builds").glob("*.json"))
        return json.loads(builds[-1].read_text()) if builds else None

    def read(self, spark, table: str) -> DataFrame:
        """Table as of the latest completed build (manifest-pinned version)."""
        manifest = self.latest_manifest()
        reader = spark.read.format("delta")
        if manifest and table in manifest.get("delta_table_versions", {}):
            reader = reader.option("versionAsOf", manifest["delta_table_versions"][table])
        return reader.load(str(self.path(table))).select(*SCHEMAS[table].fieldNames())

    def publish(self, build_id: str, manifest: dict) -> None:
        manifest["delta_table_versions"] = {t: self._written[(build_id, t)] for t in TABLES}
        builds = self.root / "_builds"
        builds.mkdir(parents=True, exist_ok=True)
        (builds / f"{build_id}.json").write_text(json.dumps(manifest, indent=2, default=str))
