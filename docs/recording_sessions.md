# Recording sessions

Code: `src/fleetpulse/validation/continuity.py`, used by `pipelines/build_session.py` and `pipelines/inspect_gaps.py`.

## Definition

A **recording session** is a stretch of the logged CAN event stream in which no two consecutive frames are more than `recording_gap_s` apart.

- Gaps are measured between consecutive valid frames across the whole ordered stream, both **inside files** and **between files**.
- A file is **not** assumed to be one continuous recording. The Turku logger can keep a file open across a long bus silence. For example, `part_3/20210119105907167768.csv` contains a 3090.36 s gap between lines 30254 and 30255.
- A recording session is a **data-continuity unit, not a trip**. A truck parked with the ignition on keeps broadcasting, so it stays in one recording session. Trip detection is a separate, later, business-level step and is not implemented.

## Threshold

- **Default: 1 s**, defined once as `fleetpulse.validation.continuity.DEFAULT_RECORDING_GAP_S`. It is used by `build_session`, `inspect_gaps`, `silver_build` and `silver_parity`, and is always configurable with `--recording-gap-s`.
- **Calibrated for this dataset and logger** (Turku part_3, python-can + PCAN-USB). **It is not a J1939 rule.** Re-audit with `pipelines.inspect_gaps` before using it on other data.
- **Strict inequality:** a gap equal to the threshold does not split.

**Evidence** (full part_3 gap audit: 2,800 files, 140,002,800 frames, streamed from the archive):
- the largest gap inside continuous traffic is 16.6 ms;
- the smallest recording silence is 6.3 s;
- there are no gaps between those two populations;
- 1 s, 3 s and 5 s all give the same 54 recording sessions (10 s → 51, 30 s → 48, 60 s → 41).

1 s sits in that empty band with about 60× margin above normal traffic and about 6× below the shortest silence. (The previous provisional default was 3 s, derived from the maximum signal age; it had only about 2× margin below the 6.3 s silences.)

## Behaviour when a gap exceeds the threshold

- `--on-gap split` (default): the current recording session is closed and a new one started. Each session gets its own `sessions/<session_id>/` with its own manifest, telemetry and report. No telemetry grid spans a break.
- `--on-gap fail`: the gap report is written, then `ContinuityError` is raised.

Every break is recorded with:
- gap duration and kind (`within_file` / `between_files`);
- source file, source line and timestamp on both sides.

It appears in `recordings/<build_id>/session_breaks.csv` and in the `preceding_break` / `following_break` fields of each `session_report.json`.

**Structural problems always raise:**
- overlapping files;
- filename order that differs from timestamp order;
- a backward time step larger than the threshold.

Smaller backward steps are counted.

## Reports

Both `recordings/<build_id>/` (build_session) and `gap_inspection/<name>/` (inspect_gaps) contain:

| File | Content |
|---|---|
| `gap_report.json` | threshold, counts, approximate quantiles, sessions, breaks, largest gaps, distribution |
| `gap_distribution.csv` | gap counts per bin (1 ms … 1 h), split within-file / between-files |
| `recording_sessions.csv` | one row per session: start, end, frames, files, largest inner gap, gaps before/after |
| `session_breaks.csv` | every gap above the threshold, with context |
| `largest_gaps.csv` | top gaps regardless of threshold |
| `files.csv` | per file: frames, time span, largest internal gap, internal breaks, gap to next file |

**Quantiles** are approximate: the upper edge of a 20-per-decade log bin, at most 12 % relative error. This keeps memory bounded for the full dataset. The maximum is exact.
