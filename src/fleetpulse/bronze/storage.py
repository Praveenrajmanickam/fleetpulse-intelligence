"""Bronze table storage. Local Parquet today; the interface is what a Delta store implements.

Layout under ``root``::

    bronze_can_frames/event_date=YYYY-MM-DD/<commit_id>__part-*.parquet
    bronze_quarantine/<commit_id>__part-*.parquet
    bronze_ingestion_log/<commit_id>.parquet
    _staging/<commit_id>/...            transient, never read by table readers

Commit protocol (Parquet has no transactions, so we emulate one):

1. Spark writes frames / quarantine / log rows for one batch into
   ``_staging/<commit_id>/``.
2. Data files are moved (``os.replace``, atomic on one filesystem) into the
   table directories, prefixed with ``<commit_id>__``.
3. The log file ``bronze_ingestion_log/<commit_id>.parquet`` is moved last.
   **Its presence is the commit.**
4. ``recover()`` (run at the start of every ingestion) deletes staging and any
   data file whose commit has no log file, i.e. leftovers of a crashed run,
   so a retry cannot duplicate rows.

A reader running concurrently with step 2 can briefly see uncommitted files;
acceptable for local development. Delta Lake removes this gap: a
``DeltaBronzeStore.commit`` would be one transaction (idempotent via
``txnAppId``/``txnVersion`` = commit id) and ``recover`` a no-op.

Small files: frames are repartitioned by ``event_date`` before writing, so a
commit produces one file per event date (capped by ``max_records_per_file``);
quarantine and log are written as one file per commit.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from pyspark.sql import DataFrame

from fleetpulse.bronze.schema import BRONZE_CAN_FRAMES, BRONZE_QUARANTINE, INGESTION_LOG

log = logging.getLogger(__name__)

SEP = "__"


class ParquetBronzeStore:
    def __init__(self, root: Path, compression: str = "zstd", max_records_per_file: int = 20_000_000):
        self.root = Path(root)
        self.frames_dir = self.root / "bronze_can_frames"
        self.quarantine_dir = self.root / "bronze_quarantine"
        self.log_dir = self.root / "bronze_ingestion_log"
        self.staging_root = self.root / "_staging"
        self.compression = compression
        self.max_records_per_file = max_records_per_file

    # --- state ----------------------------------------------------------------
    def committed_commit_ids(self) -> set[str]:
        if not self.log_dir.exists():
            return set()
        return {p.stem for p in self.log_dir.glob("*.parquet")}

    def recover(self) -> dict:
        """Remove staging and data files of commits that never reached the log."""
        removed = {"staging_dirs": 0, "orphan_files": 0}
        if self.staging_root.exists():
            for d in self.staging_root.iterdir():
                shutil.rmtree(d, ignore_errors=True)
                removed["staging_dirs"] += 1
        committed = self.committed_commit_ids()
        for table in (self.frames_dir, self.quarantine_dir):
            if not table.exists():
                continue
            for f in table.rglob("*.parquet"):
                commit_id = f.name.split(SEP, 1)[0]
                if commit_id not in committed:
                    log.warning("Removing orphan file of uncommitted %s: %s", commit_id, f)
                    f.unlink()
                    removed["orphan_files"] += 1
        return removed

    def raw_staging_dir(self, commit_id: str) -> Path:
        return self.staging_root / commit_id / "raw"

    # --- reading --------------------------------------------------------------
    def read_frames(self, spark) -> DataFrame:
        return spark.read.schema(BRONZE_CAN_FRAMES).parquet(str(self.frames_dir))

    def read_quarantine(self, spark) -> DataFrame:
        return spark.read.schema(BRONZE_QUARANTINE).parquet(str(self.quarantine_dir))

    def read_log(self, spark) -> DataFrame | None:
        if not self.committed_commit_ids():
            return None
        return spark.read.schema(INGESTION_LOG).parquet(str(self.log_dir))

    def log_rows(self, spark) -> list[dict]:
        df = self.read_log(spark)
        if df is None:
            return []
        return [r.asDict() for r in df.select("source_file", "source_sha256", "status").collect()]

    # --- writing --------------------------------------------------------------
    def commit(
        self,
        spark,
        commit_id: str,
        frames: DataFrame | None,
        quarantine: DataFrame | None,
        log_rows: list[dict],
    ) -> dict:
        stage = self.staging_root / commit_id
        opts = {"compression": self.compression}
        if frames is not None:
            (
                frames.repartition("event_date")
                .sortWithinPartitions("source_file", "source_line")
                .write.options(**opts, maxRecordsPerFile=self.max_records_per_file)
                .partitionBy("event_date")
                .parquet(str(stage / "frames"))
            )
        if quarantine is not None:
            quarantine.coalesce(1).write.options(**opts).parquet(str(stage / "quarantine"))
        spark.createDataFrame(log_rows, INGESTION_LOG).coalesce(1).write.options(**opts).parquet(str(stage / "log"))

        published = {
            "frames": self._publish(stage / "frames", self.frames_dir, commit_id),
            "quarantine": self._publish(stage / "quarantine", self.quarantine_dir, commit_id),
        }
        (log_file,) = (stage / "log").glob("part-*.parquet")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        os.replace(log_file, self.log_dir / f"{commit_id}.parquet")  # commit point
        shutil.rmtree(stage, ignore_errors=True)
        published["frame_files"] = len(published["frames"])
        published["quarantine_files"] = len(published["quarantine"])
        return published

    @staticmethod
    def _publish(src: Path, dest: Path, commit_id: str) -> list[str]:
        if not src.exists():
            return []
        out = []
        for f in sorted(src.rglob("part-*.parquet")):
            target = dest / f.parent.relative_to(src) / f"{commit_id}{SEP}{f.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(f, target)
            out.append(str(target))
        return out


def event_date_partitions(frames: DataFrame) -> list[str]:
    return [str(r.event_date) for r in frames.select("event_date").distinct().orderBy("event_date").collect()]



STORAGE_FORMATS = ("parquet", "delta")


def open_bronze_store(root: Path, storage_format: str = "parquet"):
    """Bronze store for ``storage_format``; both implement the same interface."""
    if storage_format == "parquet":
        return ParquetBronzeStore(root)
    if storage_format == "delta":
        from fleetpulse.bronze.storage_delta import DeltaBronzeStore

        return DeltaBronzeStore(root)
    raise ValueError(f"storage_format must be one of {STORAGE_FORMATS}")
