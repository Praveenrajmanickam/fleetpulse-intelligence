# Silver layer (local PySpark)

```
bronze_can_frames
   │  silver.decode      J1939 identifier + verified FMS signals   (native Spark expressions)
   ▼
silver_signal_events     one row per frame × verified signal
   │  silver.sessions    recording sessions over the frame stream   (Spark per-file pieces + driver assembly)
   │  silver.alignment   event-time as-of on a 100 ms grid, freshness (read from the WRITTEN events)
   ▼
silver_telemetry         one row per recording session × 100 ms grid point
```

```
python -m fleetpulse.pipelines.silver_build  --bronze-root data/bronze --silver-root data/silver
python -m fleetpulse.pipelines.silver_parity --archive data/raw/part_3.tar.xz \
    --include-list config/bronze_subset_part3.txt --silver-root data/silver
```

## Single source of truth

Nothing is redefined in Silver. It imports from the validated reference implementation:

| What | From |
|---|---|
| Signals, PGNs, SPNs, positions, scaling, ranges, units, FMS repetition rates | `fleetpulse.j1939.definitions.MESSAGES` |
| Raw-range status rules | `fleetpulse.j1939.signals._MEASURED_RANGES`, `SignalStatus` (same decision order as `_classify`) |
| PDU1/PDU2 threshold | `fleetpulse.j1939.identifier.PDU2_THRESHOLD` |
| Grid period and max signal age | `fleetpulse.telemetry.alignment.DEFAULT_PERIOD_MS`, `max_age_ms` (3 × FMS period) |

No Python UDFs are used. Every step is built from Spark column expressions:
- **Identifier and payload:** bit shifts and masks, little-endian byte combination.
- **Status and value:** `CASE` chains for status; scaling and offsets as arithmetic.
- **Reshaping:** `explode` of per-message signal arrays, `pivot` to frame level.
- **Ordering:** window `lag`/`lead` and running sums.
- **Alignment:** `sequence` + `explode` for grid generation and as-of coverage; broadcast range join for session assignment.

## silver_signal_events

Partitioned by `event_date`, sorted by `pgn, spn, event_timestamp`, so row-group statistics prune by signal without an SPN partition.

| Column | Type | Notes |
|---|---|---|
| event_timestamp, event_date | TIMESTAMP_NTZ, DATE | from Bronze, no timezone |
| vehicle_id | STRING | `turku-renault-t520-6x2`: **dataset-level identifier**, not decoded from CAN (VIN via BAM is not decoded) |
| recording_session_id | STRING | `<vehicle>-<session start yyyyMMddTHHmmssffffff>`, deterministic |
| can_id, priority, pgn, source_address | INT, TINYINT, INT, SMALLINT | J1939-21 |
| message, spn, signal_name, signal_unit | STRING, INT, STRING, STRING | from the definitions |
| signal_raw | BIGINT | raw integer (bit field for switches) |
| signal_value | DOUBLE | `raw × resolution + offset` **only when status = valid**; switches carry 0/1; never a substitute zero |
| signal_status | STRING | `valid`, `out_of_range`, `reserved`, `error`, `not_available`, `missing_bytes` |
| source_archive, source_file, source_line, ingestion_run_id | | Bronze lineage |
| silver_build_id | STRING | |

As in the reference, frames of a verified PGN with DLC ≠ 8 are not decoded. They are counted in the build manifest.

## Recording sessions (`silver_recording_sessions`, supporting table)

These follow the reference `validation.continuity` rules:
- A gap is measured between consecutive valid Bronze frames, inside files (by `source_line`) and between files (by first timestamp).
- A gap **greater than** `--recording-gap-s` ends the session. The default is **1.0 s**: the provisional value from the full part_3 gap audit, where normal gaps are ≤ 16.6 ms and the smallest silence is 6.3 s.
- Overlapping files, filename order ≠ time order, or a backward step larger than the threshold raise `ContinuityError`.

Sessions are **data continuity, not trips**: a vehicle parked with the bus awake stays in one session.

## silver_telemetry

Partitioned by `event_date` (of `grid_timestamp`), sorted by `recording_session_id, grid_timestamp`.

**Production contract (V1):**
- **The recording session determines the grid's lifetime:** every 100 ms from ceil(`session_start`) to floor(`session_end`). A session containing no 100 ms grid point yields no rows.
- **Each message determines only its own availability.** At grid time t, a message uses its latest frame with timestamp ≤ t (exact matches included) if t − frame_time ≤ max_age (EEC1 60 ms, EEC2 150 ms, CCVS/LFE 300 ms, HRLFC/VDHR/ET1/AMB 3000 ms). Otherwise its values are NULL and `<msg>_freshness` is `stale` or `no_observation`.
- **A stopped or absent message never truncates telemetry for other signals.**
- **No look-ahead, no interpolation, no carry-over between recording sessions.** An invalid latest frame gives NULL with its `<signal>_status`; there is no fallback to an older valid frame.

Implementation: a frame serves exactly the grid points in `[ceil(ts), min(next_ts − 1 µs, ts + max_age, grid_end)]`, where `next_ts` is the next frame of the same message in the session, found with `lead`. Those points are generated with `sequence` and joined to the grid.

| Columns | Meaning |
|---|---|
| grid_timestamp, event_date, vehicle_id, recording_session_id | |
| `<signal>` (12) | value of the frame used; DOUBLE, or TINYINT for switches |
| `<signal>_status` (12) | status of the frame used, which explains a NULL from `not_available` / `error` |
| `<msg>_age_ms` (8) | age of the frame used; NULL when none is within max_age (as in the reference) |
| `<msg>_freshness` (8) | `fresh` / `stale` (latest frame older than max_age) / `no_observation` (message absent so far in the session) |
| silver_build_id | |

No operating states, stops, efficiency, trips or business metrics: those belong to Gold.

## Rebuild / idempotency

Silver is a **full, deterministic rebuild from Bronze**:
1. Each build writes complete new tables to `_staging/<build_id>/`.
2. It publishes them by directory rename.
3. It writes `_builds/<build_id>.json`, recording the Bronze commit ids read, the configuration, counts and timings.

`recover()` at the start of every build restores a table left in `_trash` by a crash and removes leftover staging. Rebuilding from unchanged Bronze reproduces identical rows; only `silver_build_id` differs.

Delta migration: replace `ParquetSilverStore` with per-table overwrite transactions. Later, incremental builds can overwrite only affected `event_date` partitions (sessions crossing midnight must then be rebuilt as a whole).

## Parity with the reference: two separate facts

**A. Validated with exact parity** against the original pandas reference (`pipelines/silver_parity.py`, which runs the unchanged reference `build_session` on the same archive members with the same threshold):
- **Decoding:** every (source_file, source_line, signal) signal event. Timestamp, CAN ID, PGN, priority, source address, SPN, raw value and status are exact; physical values agree within tolerance.
- **Recording sessions:** start, end and frame count of every session.
- **As-of alignment:** on every grid point both implementations produce, the value, NULL position, the status of the frame used, and the age all match.

Tolerance: |Δvalue| ≤ 1e-9 + 1e-12·|reference|; |Δage| ≤ 1e-6 ms. The observed maximum difference is 0.

**B. Intentional contract difference: grid lifetime.** The reference grid spans only ceil(latest first observation) to floor(earliest last observation) over the messages present. So it
- starts late, until the slowest (1 Hz) message has appeared;
- stops early, as soon as any message type stops broadcasting.

Silver's grid spans the whole recording session. The parity tool checks that the reference grid is contained in the Silver grid, and reports the extra Silver rows separately as `silver_only_rows`. On the 8-file subset that is 162 rows (6–10 per session, at session start and end); they are not counted as mismatches.

Real-subset sessions never go stale (every session is split at gaps > 1 s). `tests/test_silver.py` therefore adds a synthetic parity test with message dropouts against `build_telemetry` (compared on the shared grid), plus contract tests:
- one message stops while others continue;
- a message is absent for the whole session;
- a message resumes after going stale;
- a session is shorter than one grid interval;
- a session ends between grid points.

## Other reference behaviour not reproduced

Reference `build_telemetry` raises `IndexError` on an empty grid (a very short session). Silver emits no rows instead.
