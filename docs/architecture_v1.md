# FleetPulse Intelligence: V1 local architecture

This document describes the current contracts, frozen before the move to Azure/Databricks. The details are in `bronze.md`, `silver.md`, `recording_sessions.md` and `signal_definitions.md`.

```
Raw source      data/raw/part_3.tar.xz      2,800 CSV logs, ~140 M CAN frames, one vehicle
    │  bronze_ingest   (archive streamed; Spark parsing; no decoding)
    ▼
Bronze          bronze_can_frames | bronze_quarantine | bronze_ingestion_log
    │  silver_build: J1939 + verified FMS decoding
    ▼
Silver          silver_signal_events   (+ silver_recording_sessions)
    │  silver_build: event-time as-of alignment, per recording session
    ▼
Silver          silver_telemetry        (100 ms grid)
```

The pandas **reference implementation** (`fleetpulse.ingestion`, `j1939`, `telemetry`, `validation`, `pipelines.build_session`) is the behavioural oracle. Bronze and Silver import its definitions and rules instead of copying them.

## Contracts

**Bronze: `bronze_can_frames`.**
- One row = one raw CAN frame, exactly as logged.
- Columns: `event_timestamp` (TIMESTAMP_NTZ, µs), `can_id` (≤ 29 bits), `dlc`, `byte_1..8` (NULL beyond the DLC, never filled), lineage, run id, and `event_date`.
- No decoding.
- Every source line can be rebuilt byte-for-byte; verified for all 400,008 subset lines.

**Silver: `silver_signal_events`.**
- One row = one frame × one verified FMS signal. Only the 12 signals in `j1939/definitions.py`, each verified against the official FMS-Standard v02.00.
- Columns: J1939 identifier fields (`pgn`, `priority`, `source_address`), `spn`, `signal_raw`, and `signal_value` (populated only when `signal_status` = valid; never a substitute zero), `signal_status`, unit, and full Bronze lineage.
- `vehicle_id` is an explicit dataset-level identifier, not decoded from the CAN frames.

**Silver: `silver_telemetry`.**
- **Rows:** one row per recording session per 100 ms grid point, from ceil(session_start) to floor(session_end).
- **Values:** each signal carries the latest frame at or before the grid time, only within its freshness limit (3 × FMS repetition period).
- **Explaining NULLs:** `<signal>_status`, `<msg>_age_ms` and `<msg>_freshness` (`fresh` / `stale` / `no_observation`).
- **Rules:** no look-ahead, no interpolation, no filling across sessions. A stopped message never truncates the other signals.

## Recording sessions and the 1-second threshold

- **Definition.** A recording session is a stretch of the complete ordered frame stream, across all files and inside files, with no gap longer than the threshold. It describes **data continuity, not trips**: a parked vehicle with the bus awake stays in one session.
- **Default: 1 s**, configurable. The full part_3 audit found:
  - continuous-traffic gaps ≤ 16.6 ms;
  - recording silences ≥ 6.3 s;
  - nothing in between;
  - 1 s, 3 s and 5 s all give the same 54 sessions.
- **The threshold is calibrated for this logger and dataset,** not a J1939 rule.

## Partitioning (current)

| Table | Partition | Sort within files |
|---|---|---|
| bronze_can_frames | `event_date` | source_file, source_line |
| silver_signal_events | `event_date` | pgn, spn, event_timestamp |
| silver_telemetry | `event_date` | recording_session_id, grid_timestamp |
| quarantine, ingestion log, sessions | none (small) | |

The data covers about 12 distinct dates, with millions of rows each. Partitioning by SPN, CAN ID, session or minute would create too many tiny files. Sorting lets Parquet statistics prune reads by signal without partitioning on it.

## Idempotency

- **Bronze is incremental per source file.** A file is identified by its path inside the archive and versioned by SHA-256:
  - unchanged → skipped;
  - same name with a new checksum → **conflict, surfaced**, never silently ingested or skipped.
  - Each batch commits by writing its log file last. `recover()` removes leftovers of crashed commits.
- **Silver is a full, deterministic rebuild from Bronze.** It writes to staging, publishes by directory rename, and records the Bronze commit ids it read in a build manifest. Session ids are derived from session start times, so a rebuild reproduces them.

## Quarantine and lineage

- **Quarantine.** Malformed records are quarantined in Bronze with `raw_record` and failure reasons:
  - a bad timestamp or CAN ID;
  - a DLC outside 0–8, or a DLC that doesn't match the number of payload bytes;
  - a byte outside 0–255;
  - too few or too many fields;
  - an empty line, or a file with the wrong header.
- **Nothing is dropped.** Per source, valid + quarantined = the independently counted raw lines, or the commit fails.
- **Lineage.** Every Bronze and Silver row carries `source_archive`, `source_file`, `source_line` and `ingestion_run_id`. Silver rows also carry `silver_build_id`.

## Known limitations

- **Timezone unknown.** The logger wrote wall-clock time without a zone, so timestamps are stored timezone-less. `event_date` is the logger's calendar date; treat it as a storage key, not a business day.
- **Local Parquet has no transactions.** Commits are emulated with staging plus renames and assume a single writer. A reader running during a commit can briefly see partial files, and many commits accumulate small files per date with no compaction. A Delta Lake store (`storage.py` in each layer) resolves all of this without touching parsing, decoding or alignment code.
- **Delta Lake is now implemented locally** as a second store behind the same interface (`--format delta`, `docs/delta_lake.md`). It matches the Parquet baseline row for row on the subset. Parquet stays the default baseline. What Delta brings:
  - ACID commits and concurrent readers;
  - idempotent appends (`txnAppId`/`txnVersion` for Bronze batches);
  - atomic table replacement or `replaceWhere` for Silver rebuilds;
  - `OPTIMIZE` for small files;
  - time travel for audit;
  - schema enforcement.
  - `event_timestamp` requires the Delta `timestampNtz` table feature.
- **Scale not yet exercised.** Everything has been validated on an 8-file, 400,008-frame subset. The full archive has not been ingested. One continuous 23.4-hour session (Feb 10–11) will be the largest unit of alignment work.

## Why Gold has not been built

Gold (operating states, stops, fuel and brake behaviour, business metrics) depends on questions the data has only started to answer. So far those have been explored on 15- and 95-minute windows in the reference analysis. The V1 goal was a trustworthy, lineage-preserving and reproducible foundation: raw frames, verified signals and aligned telemetry, each proven against the reference. Gold metrics will be designed only once that foundation runs on the cloud platform and the full dataset.
