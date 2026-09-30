# Bronze layer (local PySpark)

One Bronze row = one raw CAN frame, exactly as logged. No J1939/FMS decoding: PGNs, signal values, states and trips belong to Silver and Gold.

Code: `src/fleetpulse/bronze/` and `src/fleetpulse/pipelines/bronze_ingest.py`. The reference pipeline (`fleetpulse.ingestion`, `j1939`, `telemetry`, …) is unchanged; Bronze only imports its header constant.

```
python -m fleetpulse.pipelines.bronze_ingest  --archive data/raw/part_3.tar.xz \
    --include-list config/bronze_subset_part3.txt --root data/bronze
python -m fleetpulse.pipelines.bronze_compare --archive data/raw/part_3.tar.xz \
    --include-list config/bronze_subset_part3.txt --root data/bronze
```

## Environment

- **Java:** OpenJDK 17.0.18 from conda-forge, installed into `fleetpulse-can`. `openssl` is pinned to 3.5.8 and nothing else was upgraded. `JAVA_HOME` is set by the environment's activation.
- **PySpark:** 4.2.0 (+ py4j 0.10.9.9), installed with pip (`pip install -e ".[spark]"`).
- **pandas:** PySpark 4.2 warns that pandas 3 is not fully supported. The pandas version is left alone (the reference pipeline uses it). Instead, Bronze never passes data between Spark and pandas: Arrow interop is off and nothing uses `toPandas` or pandas UDFs. Tooling reads Bronze Parquet with pyarrow.

## Architecture

| Module | Role | Spark? |
|---|---|---|
| `archive_source` | Streams tar(.xz) members one at a time (at most one member in memory), SHA-256, stages selected members as plain files | no |
| `parsing` | Raw file text → lines → parsed records → valid frames / quarantine; pure transformations | yes |
| `ingestion_log` | Ingest / skip / conflict decisions from committed log rows | no |
| `storage` | Table layout and commit protocol (`ParquetBronzeStore`) | yes |
| `pipelines.bronze_ingest` | Orchestration, batching, reconciliation, CLI | yes |

**Archive handling.** A `.tar.xz` is one sequential compressed stream: no splits, no random access. Spark therefore never reads it. Python streams it once per run and writes each batch of members (default 50 ≈ 170 MB) to `_staging/<commit_id>/raw/`. Spark reads that batch (`wholetext`: one row per file, so exact line numbers are kept), and staging is deleted after the commit. Disk and memory are bounded by one batch. On Databricks the archive would be unpacked once into a landing zone (or read with Auto Loader); only `archive_source` changes.

**Reconciliation.** For every source, the Spark record count must equal an independent count of lines in the raw bytes, or the commit is aborted. Every record ends up valid or quarantined; none is dropped.

## Tables

### `bronze_can_frames` (partitioned by `event_date`)

| Column | Type | Notes |
|---|---|---|
| event_timestamp | TIMESTAMP_NTZ | Logged wall-clock time, microseconds, **no timezone applied** (the source has none) |
| can_id | INT | ≤ 0x1FFFFFFF |
| dlc | TINYINT | 0–8 |
| byte_1 … byte_8 | SMALLINT | 0–255; NULL beyond DLC (never filled) |
| source_archive | STRING | archive file name |
| source_file | STRING | member path, e.g. `part_3/20210119101259207093.csv` |
| source_line | BIGINT | 1-based line in the file (header = 1) |
| ingested_at | TIMESTAMP | UTC instant of the run |
| ingestion_run_id | STRING | |
| event_date | DATE | `to_date(event_timestamp)`, no timezone conversion |

`SMALLINT` is used because Spark `TINYINT` is signed (−128..127). The source line is exactly `str(event_timestamp);0x<can_id hex>;dlc;byte_1;…;byte_dlc`, and the comparison tool checks this for every row.

### `bronze_quarantine`

- `raw_record` STRING: the line as logged, CR removed.
- `source_archive`, `source_file`, `source_line`.
- `failure_reason` STRING: the first failing check.
- `failure_reasons` ARRAY<STRING>: all failing checks.
- `ingested_at`, `ingestion_run_id`.

Failure reasons, in priority order: `file_header_mismatch`, `empty_line`, `too_few_fields`, `bad_timestamp`, `bad_can_id`, `can_id_exceeds_29_bits`, `bad_dlc`, `too_many_payload_fields`, `bad_payload_byte`, `payload_byte_out_of_range`, `dlc_payload_mismatch`.

For well-formed data the rules equal the reference reader's. They deliberately differ on malformed input:
- A record with more than 8 payload fields is quarantined; the reference fails the whole file.
- A blank line is quarantined; pandas skips it silently.
- A wrong header quarantines every record of that file; the reference raises.

### `bronze_ingestion_log`

- **Source:** `source_archive`, `source_file`, `source_sha256`, `source_size_bytes`, `header_ok`.
- **Counts:** `record_count`, `valid_record_count`, `invalid_record_count`.
- **Time range:** `first_event_timestamp` and `last_event_timestamp` (both NTZ).
- **Run and commit:** `ingestion_run_id`, `commit_id`, `ingested_at`.
- **Outcome:** `status` (`ingested` | `skipped_already_ingested` | `conflict_checksum_changed`) and `message`.

## Idempotency

- **Identity.** A source is identified by `source_file` (member path) and versioned by SHA-256. The archive name is not part of the identity, so re-packing the same files does not duplicate them.
- **Rerun of an unchanged file:** logged as `skipped_already_ingested`; no rows are written.
- **Same name, different SHA-256:** logged as `conflict_checksum_changed` and never ingested. With `--on-conflict fail` (default) the run stops with `SourceConflictError`, exit code 2. With `--on-conflict skip` it logs the conflict and continues with other files.

**Commit protocol** (Parquet has no transactions):
1. The batch is written to staging.
2. Data files are moved into the tables, named `<commit_id>__part-…`.
3. The log file `<commit_id>.parquet` is moved last; **its presence is the commit**.
4. At the start of every run, `recover()` deletes staging and any data file whose commit has no log file. A crashed run cannot leave duplicates behind.

This assumes a single writer.

## Partitioning: `event_date`

- **Row counts.** part_3 covers 23.9 calendar days but only about 12 distinct dates with data, holding 140 M frames. That is millions of rows per partition: large enough for efficient files, few enough for cheap listing.
- **Query pattern.** Every downstream consumer (Silver decoding, sessions, fuel) filters by time, and a date prunes naturally.
- **Rejected alternatives:**
  - CAN ID (68 values) × date → hundreds of tiny directories.
  - Minute → about 2,750+ partitions.
  - Source file (2,800) → one partition per input: the small-file problem itself.
  - PGN → requires decoding, which is a Silver concern.
- **Caveat.** `event_date` is the date on the logger's clock, whose timezone is unknown. It is a storage key, not a business "day".

## Small files

Frames are repartitioned by `event_date` before writing, so each commit writes **one file per event date it touches**, not one per input CSV. Files are sorted by (`source_file`, `source_line`), which is also time order, and compressed with zstd. `maxRecordsPerFile` (20 M) caps file size. Quarantine and log are one file per commit.

Across many commits a busy date still accumulates one file per commit. Compaction (Delta `OPTIMIZE`) is the intended fix; a larger `--batch-files` reduces it locally.

## Moving to Delta Lake / Databricks

`ParquetBronzeStore` is the only storage-specific code. A `DeltaBronzeStore` would implement the same `commit` / `recover` / `read_*` / `log_rows`:

- `commit` becomes one Delta transaction: append frames and quarantine, `MERGE` the log, idempotent through `txnAppId` = run and `txnVersion` = batch.
- `recover` becomes a no-op.
- `event_timestamp` needs the Delta `timestampNtz` table feature.

`parsing`, `ingestion_log` and the schemas stay unchanged.
