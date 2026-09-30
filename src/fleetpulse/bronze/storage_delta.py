"""Bronze tables stored as Delta Lake tables (same contract as ParquetBronzeStore).

Tables (path-based, under ``root``)::

    bronze_can_frames      partitioned by event_date   schema BRONZE_CAN_FRAMES
    bronze_quarantine                                  schema BRONZE_QUARANTINE
    bronze_ingestion_log                               schema INGESTION_LOG

All three are created up front with the explicit schemas, so Delta enforces
column names, types and NOT NULL from version 0.

Commit protocol. Each Delta write is an ACID transaction on ONE table; Delta
has no multi-table transaction. A Bronze batch touches three tables, so:

1. append the batch's ingested sources to the log with status ``pending``
   (write-ahead intent);
2. append frames, then quarantine - each a single atomic Delta commit, made
   idempotent with ``txnAppId=<commit_id>``/``txnVersion=0`` (a replay of the
   same commit is skipped by Delta instead of duplicating rows);
3. append the final log rows (``ingested`` / ``skipped_already_ingested`` /
   ``conflict_checksum_changed``) - **the commit point**.

``recover()`` finds ``pending`` intents without a final row (a crash between 1
and 3), deletes exactly those sources' rows of that run from frames and
quarantine (one Delta DELETE transaction each) and appends ``rolled_back``
rows. Ingestion decisions only count ``ingested`` rows, so the extra
statuses never change what is skipped or re-ingested.

Unlike the Parquet store, a failed or interrupted write never leaves
partial files visible to readers: data files not referenced by a committed
log version are ignored (and removable with VACUUM).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from fleetpulse import delta_support
from fleetpulse.bronze.schema import BRONZE_CAN_FRAMES, BRONZE_QUARANTINE, INGESTION_LOG, STATUS_INGESTED

log = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_ROLLED_BACK = "rolled_back"


def _spark() -> SparkSession:
    spark = SparkSession.getActiveSession()
    if spark is None:
        raise RuntimeError("DeltaBronzeStore needs an active SparkSession")
    return spark


class DeltaBronzeStore:
    format = "delta"

    def __init__(self, root: Path, compression: str = "zstd", max_records_per_file: int = 20_000_000):
        self.root = Path(root).resolve()  # Delta needs absolute table locations
        self.compression = compression  # same codec as ParquetBronzeStore
        self.frames_dir = self.root / "bronze_can_frames"
        self.quarantine_dir = self.root / "bronze_quarantine"
        self.log_dir = self.root / "bronze_ingestion_log"
        self.staging_root = self.root / "_staging"
        self.max_records_per_file = max_records_per_file

    # --- tables ---------------------------------------------------------------
    def ensure_tables(self, spark) -> None:
        delta_support.ensure_table(spark, self.frames_dir, BRONZE_CAN_FRAMES, ("event_date",))
        delta_support.ensure_table(spark, self.quarantine_dir, BRONZE_QUARANTINE)
        delta_support.ensure_table(spark, self.log_dir, INGESTION_LOG)

    def raw_staging_dir(self, commit_id: str) -> Path:
        return self.staging_root / commit_id / "raw"

    # --- reading --------------------------------------------------------------
    def _read(self, spark, path: Path, schema) -> DataFrame:
        return spark.read.format("delta").load(str(path)).select(*schema.fieldNames())

    def read_frames(self, spark) -> DataFrame:
        return self._read(spark, self.frames_dir, BRONZE_CAN_FRAMES)

    def read_quarantine(self, spark) -> DataFrame:
        return self._read(spark, self.quarantine_dir, BRONZE_QUARANTINE)

    def read_log(self, spark) -> DataFrame | None:
        if not delta_support.is_delta_table(self.log_dir):
            return None
        return self._read(spark, self.log_dir, INGESTION_LOG)

    def log_rows(self, spark) -> list[dict]:
        df = self.read_log(spark)
        if df is None:
            return []
        return [r.asDict() for r in df.select("source_file", "source_sha256", "status").collect()]

    def committed_commit_ids(self) -> set[str]:
        df = self.read_log(_spark())
        if df is None:
            return set()
        return {r.commit_id for r in df.where(F.col("status") != STATUS_PENDING).select("commit_id").distinct().collect()}

    # --- recovery -------------------------------------------------------------
    def recover(self) -> dict:
        spark = _spark()
        removed = {"staging_dirs": 0, "rolled_back_sources": 0, "deleted_frames": 0, "deleted_quarantine": 0}
        if self.staging_root.exists():
            for d in self.staging_root.iterdir():
                shutil.rmtree(d, ignore_errors=True)
                removed["staging_dirs"] += 1
        logdf = self.read_log(spark)
        if logdf is None:
            return removed
        key = ["commit_id", "source_file"]
        pending = logdf.where(F.col("status") == STATUS_PENDING)
        final = logdf.where(F.col("status") != STATUS_PENDING).select(*key)
        orphans = pending.join(final, key, "left_anti").collect()
        if not orphans:
            return removed
        by_run: dict[str, list[str]] = {}
        for r in orphans:
            by_run.setdefault(r.ingestion_run_id, []).append(r.source_file)
        for run_id, files in by_run.items():
            cond = (F.col("ingestion_run_id") == run_id) & F.col("source_file").isin(files)
            for path, counter in ((self.frames_dir, "deleted_frames"), (self.quarantine_dir, "deleted_quarantine")):
                delta_support.table(spark, path).delete(cond)
                m = delta_support.last_operation_metrics(spark, path)
                if m["operation"] == "DELETE":
                    removed[counter] += int(m.get("numDeletedRows", 0) or 0)
        rollback = [
            {**r.asDict(), "status": STATUS_ROLLED_BACK, "message": "commit interrupted before its final log row; rows deleted"}
            for r in orphans
        ]
        spark.createDataFrame(rollback, INGESTION_LOG).write.format("delta").mode("append").save(str(self.log_dir))
        removed["rolled_back_sources"] = len(orphans)
        log.warning("Rolled back %d source(s) of interrupted commits: %s", len(orphans), removed)
        return removed

    # --- writing --------------------------------------------------------------
    def _append(self, df: DataFrame, path: Path, txn_app_id: str) -> dict:
        (
            df.write.format("delta")
            .mode("append")
            .option("txnAppId", txn_app_id)
            .option("txnVersion", 0)
            .option("maxRecordsPerFile", self.max_records_per_file)
            .option("compression", self.compression)
            .save(str(path))
        )
        return delta_support.last_operation_metrics(df.sparkSession, path)

    def _append_log(self, spark, rows: list[dict], txn_app_id: str) -> None:
        if rows:
            self._append(spark.createDataFrame(rows, INGESTION_LOG).coalesce(1), self.log_dir, txn_app_id)

    def commit(self, spark, commit_id: str, frames, quarantine, log_rows: list[dict]) -> dict:
        import time

        timings = {}
        t = time.perf_counter()
        self.ensure_tables(spark)
        intents = [{**r, "status": STATUS_PENDING} for r in log_rows if r["status"] == STATUS_INGESTED]
        self._append_log(spark, intents, f"{commit_id}:pending")
        timings["ensure_tables_and_pending_log_s"] = round(time.perf_counter() - t, 2)
        out = {"frame_files": 0, "quarantine_files": 0, "versions": {}, "timings": timings}
        t = time.perf_counter()
        if frames is not None:
            m = self._append(
                frames.select(*BRONZE_CAN_FRAMES.fieldNames()).repartition("event_date")
                .sortWithinPartitions("source_file", "source_line"),
                self.frames_dir, f"{commit_id}:frames",
            )
            out["frame_files"] = int(m.get("numFiles", 0) or 0)
            out["versions"]["bronze_can_frames"] = m["version"]
        timings["frames_write_s"] = round(time.perf_counter() - t, 2)
        t = time.perf_counter()
        if quarantine is not None:
            m = self._append(quarantine.select(*BRONZE_QUARANTINE.fieldNames()).coalesce(1), self.quarantine_dir,
                             f"{commit_id}:quarantine")
            out["quarantine_files"] = int(m.get("numFiles", 0) or 0)
            out["versions"]["bronze_quarantine"] = m["version"]
        timings["quarantine_write_s"] = round(time.perf_counter() - t, 2)
        t = time.perf_counter()
        self._append_final_log(spark, log_rows, commit_id)  # commit point
        timings["final_log_s"] = round(time.perf_counter() - t, 2)
        shutil.rmtree(self.staging_root / commit_id, ignore_errors=True)
        return out

    def _append_final_log(self, spark, log_rows: list[dict], commit_id: str) -> None:
        self._append_log(spark, log_rows, f"{commit_id}:log")
