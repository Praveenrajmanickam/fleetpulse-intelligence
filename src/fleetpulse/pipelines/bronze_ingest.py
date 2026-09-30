"""Bronze ingestion: tar(.xz) archive -> bronze_can_frames / bronze_quarantine / bronze_ingestion_log.

Usage (development subset)::

    python -m fleetpulse.pipelines.bronze_ingest \\
        --archive data/raw/part_3.tar.xz \\
        --include-list config/bronze_subset_part3.txt \\
        --root data/bronze

Flow per run:

1. ``store.recover()`` removes leftovers of crashed runs.
2. The archive is streamed once (one member in memory at a time). Each member
   is checksummed and checked against the committed ingestion log:
   new -> staged for ingestion; unchanged -> logged as skipped; same name with
   a different checksum -> logged as a conflict and, with ``--on-conflict
   fail`` (default), the run stops with SourceConflictError after committing
   what it has.
3. Every ``--batch-files`` staged members are parsed by Spark and committed
   atomically (see bronze.storage). Staging is deleted after each commit, so
   disk use is bounded by one batch.
4. Per source, Spark's record count is reconciled with an independent count
   of lines in the raw bytes; a mismatch fails the batch.

No J1939/FMS decoding happens here.
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

from pyspark import StorageLevel
from pyspark.sql import functions as F

from fleetpulse.bronze import parsing
from fleetpulse.bronze.archive_source import SourceFile, iter_sources, read_member_list, stage
from fleetpulse.bronze.ingestion_log import Decision, IngestionLedger, SourceConflictError
from fleetpulse.bronze.schema import STATUS_CONFLICT, STATUS_INGESTED, STATUS_SKIPPED
from fleetpulse.bronze.storage import STORAGE_FORMATS, ParquetBronzeStore, open_bronze_store
from fleetpulse.ingestion.can_csv import EXPECTED_HEADER

log = logging.getLogger("fleetpulse.bronze")

ON_CONFLICT = ("fail", "skip")


def new_run_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def count_records(data: bytes) -> tuple[int, bool]:
    """Independent (non-Spark) record count and header check for reconciliation."""
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    header_ok = bool(lines) and lines[0].rstrip(b"\r") == EXPECTED_HEADER.encode()
    return max(len(lines) - 1, 0), header_ok


def _log_row(src: SourceFile, run_id: str, commit_id: str, ingested_at, status: str, **extra) -> dict:
    row = {
        "source_archive": src.source_archive,
        "source_file": src.source_file,
        "source_sha256": src.sha256,
        "source_size_bytes": src.size_bytes,
        "header_ok": None,
        "record_count": None,
        "valid_record_count": None,
        "invalid_record_count": None,
        "first_event_timestamp": None,
        "last_event_timestamp": None,
        "ingestion_run_id": run_id,
        "commit_id": commit_id,
        "ingested_at": ingested_at,
        "status": status,
        "message": None,
    }
    row.update(extra)
    return row


class BronzeIngestion:
    def __init__(self, spark, store: ParquetBronzeStore, batch_files: int = 50, on_conflict: str = "fail"):
        """``store``: ParquetBronzeStore or DeltaBronzeStore (same interface)."""
        if on_conflict not in ON_CONFLICT:
            raise ValueError(f"on_conflict must be one of {ON_CONFLICT}")
        self.spark, self.store = spark, store
        self.batch_files, self.on_conflict = batch_files, on_conflict

    def run(self, archive: Path, include: list[str] | None = None, run_id: str | None = None) -> dict:
        t0 = time.perf_counter()
        recovered = self.store.recover()
        ledger = IngestionLedger.from_log_rows(self.store.log_rows(self.spark))
        run_id = run_id or new_run_id()
        ingested_at = datetime.now(timezone.utc)
        summary = {
            "ingestion_run_id": run_id,
            "ingested_at": ingested_at.isoformat(),
            "recovered": recovered,
            "commits": [],
            "sources": {STATUS_INGESTED: 0, STATUS_SKIPPED: 0, STATUS_CONFLICT: 0},
            "records": 0,
            "valid_records": 0,
            "invalid_records": 0,
            # Instrumentation only (wall clock): where ingestion time goes.
            "timings": {"archive_read_s": 0.0, "python_count_s": 0.0, "stage_write_s": 0.0, "commits_s": 0.0},
            "phase_windows": {},
        }
        run_t0_epoch = time.time()
        timings = summary["timings"]
        batch: list[tuple[SourceFile, str, int, bool]] = []  # source (no data), staged name, count, header
        pending: list[dict] = []  # skip/conflict log rows
        conflicts: list[dict] = []
        seq = 0

        def commit_id() -> str:
            return f"{run_id}-b{len(summary['commits']) + 1:05d}"

        sources = iter_sources(archive, include)
        while True:
            ta = time.perf_counter()
            src = next(sources, None)  # xz decompression + tar member read + SHA-256
            timings["archive_read_s"] += time.perf_counter() - ta
            if src is None:
                break
            decision = ledger.decide(src.source_file, src.sha256)
            if decision is Decision.INGEST:
                ledger.record_ingested(src.source_file, src.sha256)
                tc = time.perf_counter()
                n, header_ok = count_records(src.data)
                timings["python_count_s"] += time.perf_counter() - tc
                ts_ = time.perf_counter()
                name = stage(src, self.store.raw_staging_dir(commit_id()), seq)
                timings["stage_write_s"] += time.perf_counter() - ts_
                seq += 1
                light = SourceFile(src.source_archive, src.source_file, src.sha256, src.size_bytes, b"")
                batch.append((light, name, n, header_ok))
            elif decision is Decision.SKIP:
                pending.append(_log_row(src, run_id, commit_id(), ingested_at, STATUS_SKIPPED))
                summary["sources"][STATUS_SKIPPED] += 1
                log.info("Skip unchanged %s", src.source_file)
            else:
                known = sorted(ledger.ingested[src.source_file])
                msg = f"sha256 {src.sha256} differs from ingested {known}; not ingested"
                pending.append(_log_row(src, run_id, commit_id(), ingested_at, STATUS_CONFLICT, message=msg))
                conflicts.append({"source_file": src.source_file, "new_sha256": src.sha256, "ingested_sha256": known})
                summary["sources"][STATUS_CONFLICT] += 1
                log.error("CONFLICT %s: %s", src.source_file, msg)
                if self.on_conflict == "fail":
                    break
            if len(batch) >= self.batch_files:
                self._commit(commit_id(), batch, pending, run_id, ingested_at, summary)
                batch, pending = [], []

        if batch or pending:
            self._commit(commit_id(), batch, pending, run_id, ingested_at, summary)
        summary["runtime_s"] = round(time.perf_counter() - t0, 2)
        summary["phase_windows"]["bronze"] = [run_t0_epoch, time.time()]
        for k in timings:
            timings[k] = round(timings[k], 2)
        summary["conflicts"] = conflicts
        if conflicts and self.on_conflict == "fail":
            raise SourceConflictError(conflicts)
        return summary

    def _commit(self, commit_id, batch, pending, run_id, ingested_at, summary) -> None:
        t0 = time.perf_counter()
        t0_epoch = time.time()
        spark_parse_s = 0.0
        log_rows = list(pending)
        frames = quarantine = None
        parsed = None
        if batch:
            mapping = self.spark.createDataFrame(
                [(name, s.source_archive, s.source_file) for s, name, _, _ in batch],
                "staged_name string, source_archive string, source_file string",
            )
            files = parsing.read_staged_files(self.spark, str(self.store.raw_staging_dir(commit_id)))
            files = files.join(F.broadcast(mapping), "staged_name").drop("staged_name").select(
                "*",
                F.lit(ingested_at).cast("timestamp").alias("ingested_at"),
                F.lit(run_id).alias("ingestion_run_id"),
            )
            parsed = parsing.parse_records(parsing.explode_lines(files)).persist(StorageLevel.MEMORY_AND_DISK)
            stats = {r.source_file: r.asDict() for r in parsing.source_summary(parsed).collect()}
            spark_parse_s = time.perf_counter() - t0  # read staged text + explode + parse + persist + summary

            n_valid = n_invalid = 0
            for s, _, expected, header_ok in batch:
                st = stats.get(s.source_file, {})
                records = st.get("record_count") or 0
                if records != expected:
                    raise RuntimeError(
                        f"{s.source_file}: Spark parsed {records} records, raw file has {expected}; aborting commit"
                    )
                valid, invalid = st.get("valid_record_count") or 0, st.get("invalid_record_count") or 0
                n_valid, n_invalid = n_valid + valid, n_invalid + invalid
                log_rows.append(
                    _log_row(
                        s, run_id, commit_id, ingested_at, STATUS_INGESTED,
                        header_ok=header_ok, record_count=records,
                        valid_record_count=valid, invalid_record_count=invalid,
                        first_event_timestamp=st.get("first_event_timestamp"),
                        last_event_timestamp=st.get("last_event_timestamp"),
                    )
                )
            frames = parsing.to_bronze(parsed) if n_valid else None
            quarantine = parsing.to_quarantine(parsed) if n_invalid else None
            summary["records"] += n_valid + n_invalid
            summary["valid_records"] += n_valid
            summary["invalid_records"] += n_invalid
            summary["sources"][STATUS_INGESTED] += len(batch)

        for r in log_rows:
            r["commit_id"] = commit_id
        tw = time.perf_counter()
        published = self.store.commit(self.spark, commit_id, frames, quarantine, log_rows)
        store_commit_s = time.perf_counter() - tw  # frames/quarantine/log writes (Spark work + commit)
        if parsed is not None:
            parsed.unpersist()
        summary["timings"]["commits_s"] += time.perf_counter() - t0
        summary["phase_windows"][commit_id] = [t0_epoch, time.time()]
        summary["commits"].append(
            {
                "commit_id": commit_id,
                "sources_ingested": len(batch),
                "log_rows": len(log_rows),
                "frame_files": published["frame_files"],
                "quarantine_files": published["quarantine_files"],
                "seconds": round(time.perf_counter() - t0, 2),
                "spark_parse_s": round(spark_parse_s, 2),
                "store_commit_s": round(store_commit_s, 2),
                "store_timings": published.get("timings", {}),
            }
        )
        log.info("Committed %s: %d sources, %d log rows in %.1f s",
                 commit_id, len(batch), len(log_rows), time.perf_counter() - t0)


def main(argv: list[str] | None = None) -> int:
    from fleetpulse.bronze.spark import get_spark

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--include-list", type=Path, help="text file of member paths (default: all members)")
    parser.add_argument("--root", type=Path, default=Path("data/bronze"))
    parser.add_argument("--batch-files", type=int, default=50)
    parser.add_argument("--on-conflict", choices=ON_CONFLICT, default="fail")
    parser.add_argument("--format", choices=STORAGE_FORMATS, default="parquet", help="table storage format")
    parser.add_argument("--master", default="local[4]")
    parser.add_argument("--driver-memory", default="2g")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    include = read_member_list(args.include_list) if args.include_list else None
    spark = get_spark(master=args.master, driver_memory=args.driver_memory)
    store = open_bronze_store(args.root, args.format)
    try:
        summary = BronzeIngestion(spark, store, args.batch_files, args.on_conflict).run(args.archive, include)
        code = 0
    except SourceConflictError as e:
        log.error("%s", e)
        summary, code = {"error": str(e), "conflicts": e.conflicts}, 2
    finally:
        spark.stop()
    reports = args.root / "_run_reports"
    reports.mkdir(parents=True, exist_ok=True)
    name = summary.get("ingestion_run_id", new_run_id() + "-failed")
    (reports / f"{name}.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
