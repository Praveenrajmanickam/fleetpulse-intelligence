"""Session pipeline: raw files -> recording sessions -> aligned telemetry per session.

Usage (select from the archive, extract without overwriting, then build)::

    python -m fleetpulse.pipelines.build_session \\
        --archive data/raw/part_3.tar.xz \\
        --anchor 20210119101259207093.csv --before 7 --after 7

or with already-extracted files::

    python -m fleetpulse.pipelines.build_session data/raw/part_3/2021011910*.csv

Steps: per-file decode (decode_can_file.run) -> gap scan of the complete
ordered event stream, inside and between files (validation.continuity) ->
split into recording sessions at gaps > ``--recording-gap-s`` (or fail with
``--on-gap fail``) -> per session: raw SHA-256 manifest, verified signal
tables clipped to the session, telemetry aligned onto a regular grid.

Recording sessions are data-continuity units, not trips.

Outputs::

    <out>/recordings/<build_id>/          gap_report.json, gap_distribution.csv,
                                          recording_sessions.csv, session_breaks.csv,
                                          largest_gaps.csv, files.csv, build_report.json
    <out>/sessions/<session_id>/          one directory per recording session:
        raw_manifest.csv                  file pieces in this session + sha256
        telemetry.parquet                 aligned, verified signals only
        session_report.json               recording boundaries, alignment, schema, coverage
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from fleetpulse import __version__
from fleetpulse.ingestion.archive import extract_members, list_csv_members, select_window
from fleetpulse.j1939.definitions import MESSAGES, SIGNALS
from fleetpulse.pipelines import decode_can_file
from fleetpulse.telemetry.alignment import DEFAULT_PERIOD_MS, alignment_config, build_telemetry
from fleetpulse.validation.continuity import (
    DEFAULT_RECORDING_GAP_S,
    ContinuityError,
    assemble,
    summarise_file,
    write_report,
)

log = logging.getLogger("fleetpulse.session")

# Project-wide default (1 s, calibrated on the part_3 gap audit); see
# validation.continuity.DEFAULT_RECORDING_GAP_S.
ON_GAP_CHOICES = ("split", "fail")


def _session_id(part: str, start: pd.Timestamp, end: pd.Timestamp) -> str:
    return f"{part}_{start:%Y%m%dT%H%M%S}_{end:%H%M%S}"


def _read_frame_times(report: dict) -> pd.DataFrame:
    frames = pd.read_parquet(report["outputs"]["frames"], columns=["timestamp", "source_line", "is_valid"])
    return frames[frames["is_valid"]]


def build_session(
    inputs: list[Path],
    output_dir: Path,
    raw_root: Path,
    session_id: str | None = None,
    period_ms: int = DEFAULT_PERIOD_MS,
    recording_gap_s: float = DEFAULT_RECORDING_GAP_S,
    on_gap: str = "split",
) -> dict:
    if on_gap not in ON_GAP_CHOICES:
        raise ValueError(f"on_gap must be one of {ON_GAP_CHOICES}")
    reports = [decode_can_file.run(p, output_dir, raw_root) for p in sorted(inputs)]
    by_file = {Path(r["run"]["input_file"]).name: r for r in reports}

    summaries = []
    for name, r in by_file.items():
        t = _read_frame_times(r)
        summaries.append(summarise_file(name, t["timestamp"], t["source_line"], recording_gap_s))
    result = assemble(summaries, recording_gap_s)

    part = Path(reports[0]["outputs"]["frames"]).parent.name
    build_id = _session_id(part, result.sessions["start"].min(), result.sessions["end"].max())
    rec_dir = output_dir / "recordings" / build_id
    rec_paths = write_report(result, rec_dir)
    log.info(
        "Gap scan: %d gaps, max %.4f s; %d gap(s) > %.3f s -> %d recording session(s)",
        result.stats["gaps_measured"], result.stats["max_gap_s"], len(result.breaks),
        recording_gap_s, len(result.sessions),
    )
    for b in result.breaks:
        log.warning(
            "Recording gap %.3f s (%s): %s line %d @ %s -> %s line %d @ %s",
            b.gap_s, b.kind, b.before_file, b.before_source_line, b.before_timestamp,
            b.after_file, b.after_source_line, b.after_timestamp,
        )
    if result.breaks and on_gap == "fail":
        raise ContinuityError(
            f"{len(result.breaks)} recording gap(s) > {recording_gap_s} s; see {rec_paths['session_breaks']}"
        )

    all_tables = {
        m.key: pd.concat(
            [pd.read_parquet(r["outputs"][f"signals_{m.key}"]) for r in reports], ignore_index=True
        ).sort_values("timestamp", ignore_index=True)
        for m in MESSAGES
    }

    session_reports = []
    for s in result.sessions.itertuples():
        sid = _session_id(part, s.start, s.end)
        if session_id:
            sid = session_id if len(result.sessions) == 1 else f"{session_id}_r{s.session_index:02d}"
        pieces = result.pieces[result.pieces["session_index"] == s.session_index]
        session_reports.append(
            _write_session(sid, s, pieces, result, by_file, all_tables, output_dir, period_ms, build_id)
        )

    build_report = {
        "run": {
            "pipeline": "build_session",
            "version": __version__,
            "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "build_id": build_id,
        },
        "recording_gap_s": recording_gap_s,
        "on_gap": on_gap,
        "input_files": len(reports),
        "gap_stats": result.stats,
        "breaks": [b.to_dict() for b in result.breaks],
        "sessions": [
            {k: sr[k] for k in ("session_id", "first_timestamp", "last_timestamp", "duration_s",
                                "total_can_frames", "telemetry_rows")}
            for sr in session_reports
        ],
        "outputs": rec_paths,
    }
    (rec_dir / "build_report.json").write_text(json.dumps(build_report, indent=2, default=str))
    return build_report


def _write_session(sid, s, pieces, result, by_file, all_tables, output_dir, period_ms, build_id) -> dict:
    session_dir = output_dir / "sessions" / sid
    session_dir.mkdir(parents=True, exist_ok=True)

    manifest = pieces.assign(
        path=[by_file[f]["run"]["input_file"] for f in pieces["file"]],
        sha256=[by_file[f]["run"]["input_sha256"] for f in pieces["file"]],
        bytes=[by_file[f]["run"]["input_bytes"] for f in pieces["file"]],
        file_frames=[by_file[f]["totals"]["can_frames"] for f in pieces["file"]],
        invalid_frames_in_file=[by_file[f]["quality"]["invalid_rows"] for f in pieces["file"]],
    ).drop(columns=["session_index"])

    tables = {}
    for m in MESSAGES:
        t = all_tables[m.key]
        t = t[(t["timestamp"] >= s.start) & (t["timestamp"] <= s.end)]
        if t.empty:
            log.warning("%s: no %s frames; message left out of telemetry", sid, m.acronym)
        else:
            tables[m.key] = t
    telemetry = build_telemetry(tables, period_ms) if tables else pd.DataFrame()

    paths = {
        "manifest": session_dir / "raw_manifest.csv",
        "telemetry": session_dir / "telemetry.parquet",
        "report": session_dir / "session_report.json",
    }
    manifest.to_csv(paths["manifest"], index=False)
    telemetry.to_parquet(paths["telemetry"], index=False)

    specs = {x.name: x for x in SIGNALS}
    schema = []
    for col, dtype in telemetry.dtypes.items():
        entry = {"column": col, "dtype": str(dtype)}
        if col in specs:
            x = specs[col]
            entry.update(pgn=x.pgn, spn=x.spn, unit=x.unit, states=x.states or None)
        elif col.endswith("_age_ms"):
            entry.update(unit="ms", note="age of the source frame used for this row")
        schema.append(entry)

    idx = s.session_index
    report = {
        "run": {
            "pipeline": "build_session",
            "version": __version__,
            "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session_id": sid,
            "build_id": build_id,
        },
        "session_id": sid,
        "raw_files": int(manifest["file"].nunique()),
        "first_timestamp": str(s.start),
        "last_timestamp": str(s.end),
        "duration_s": float(s.duration_s),
        "total_can_frames": int(s.frames),
        "invalid_frames_in_files": int(manifest.drop_duplicates("file")["invalid_frames_in_file"].sum()),
        "recording": {
            "kind": "recording session (data continuity, not a trip)",
            "session_index": int(idx),
            "sessions_in_build": int(len(result.sessions)),
            "recording_gap_s": result.recording_gap_s,
            "max_gap_inside_session_s": float(s.max_gap_s),
            "preceding_break": result.breaks[idx - 1].to_dict() if idx > 0 else None,
            "following_break": result.breaks[idx].to_dict() if idx < len(result.breaks) else None,
        },
        "decoded_frames": {m.acronym: int(len(tables[m.key])) if m.key in tables else 0 for m in MESSAGES},
        "alignment": alignment_config(period_ms),
        "telemetry_rows": int(len(telemetry)),
        "telemetry_coverage_pct": {
            x.name: round(100 * telemetry[x.name].notna().mean(), 3) if x.name in telemetry else 0.0 for x in SIGNALS
        },
        "telemetry_schema": schema,
        "outputs": {k: str(v) for k, v in paths.items()},
    }
    paths["report"].write_text(json.dumps(report, indent=2, default=str))
    log.info("Session %s: %.1f s, %d frames, %d telemetry rows", sid, s.duration_s, s.frames, len(telemetry))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("inputs", nargs="*", type=Path, help="extracted raw CAN CSV files")
    parser.add_argument("--archive", type=Path, help="tar(.xz) archive to extract a window from")
    parser.add_argument("--anchor", help="file name the window is centred on")
    parser.add_argument("--before", type=int, default=7)
    parser.add_argument("--after", type=int, default=7)
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--session-id")
    parser.add_argument("--period-ms", type=int, default=DEFAULT_PERIOD_MS)
    parser.add_argument(
        "--recording-gap-s", type=float, default=DEFAULT_RECORDING_GAP_S,
        help="a timestamp gap longer than this ends a recording session (default %(default)s)",
    )
    parser.add_argument("--on-gap", choices=ON_GAP_CHOICES, default="split",
                        help="split into recording sessions (default) or fail")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    inputs = list(args.inputs)
    if args.archive:
        if not args.anchor:
            parser.error("--archive requires --anchor")
        window = select_window(list_csv_members(args.archive), args.anchor, args.before, args.after)
        inputs += extract_members(args.archive, window, args.raw_root)
    if not inputs:
        parser.error("no input files")
    build_session(
        inputs, args.output_dir, args.raw_root, args.session_id, args.period_ms,
        args.recording_gap_s, args.on_gap,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
