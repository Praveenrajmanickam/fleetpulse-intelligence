"""Row/signal-level parity: PySpark Silver vs the pandas reference pipeline.

Usage::

    python -m fleetpulse.pipelines.silver_parity \\
        --archive data/raw/part_3.tar.xz --include-list config/bronze_subset_part3.txt \\
        --silver-root data/silver

The reference oracle is the unchanged ``pipelines.build_session`` run on the
same archive members (extracted to a temporary directory) with the same
recording-gap threshold. Compared:

* signal events: every (source_file, source_line, signal) present on both
  sides; timestamp, CAN ID, PGN, priority, source address, SPN, raw value and
  status exactly; physical value within tolerance; counts per signal;
* recording sessions: start, end and frame count of every session;
* telemetry, per session: the Silver grid is exactly ceil(session_start)..
  floor(session_end); the reference grid is contained in it; on every shared
  grid point each reference cell matches (value within tolerance, identical
  NULL positions, status and age where the reference has a sample). Silver rows
  outside the reference grid are reported as an intentional difference: the
  reference truncates a session when one message type stops.

Numerical tolerance (documented): |silver - reference| <= 1e-9 + 1e-12 * |reference|
for physical values; 1e-6 ms for ages. Both sides compute ``raw * resolution
+ offset`` in IEEE-754 double, so exact equality is expected.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from fleetpulse.bronze.archive_source import iter_sources, read_member_list
from fleetpulse.j1939.definitions import MESSAGES
from fleetpulse.pipelines.build_session import build_session
from fleetpulse.silver.storage import EVENTS, SESSIONS, TELEMETRY_TABLE
from fleetpulse.validation.continuity import DEFAULT_RECORDING_GAP_S

ABS_TOL, REL_TOL, AGE_TOL_MS = 1e-9, 1e-12, 1e-6
log = logging.getLogger("fleetpulse.silver_parity")


def _read(root: Path, table: str) -> pd.DataFrame:
    return ds.dataset(root / table, format="parquet", partitioning="hive").to_table().to_pandas()


def _close(a: pd.Series, b: pd.Series, abs_tol=ABS_TOL, rel_tol=REL_TOL) -> np.ndarray:
    a = pd.to_numeric(a, errors="coerce").astype("float64").to_numpy()
    b = pd.to_numeric(b, errors="coerce").astype("float64").to_numpy()
    both_nan = np.isnan(a) & np.isnan(b)
    return both_nan | (np.abs(a - b) <= abs_tol + rel_tol * np.abs(np.nan_to_num(b)))


def run_reference(archive: Path, members: list[str], workdir: Path, recording_gap_s: float) -> tuple[dict, Path]:
    raw_root = workdir / "raw"
    paths = []
    for src in iter_sources(archive, members):
        p = raw_root / src.source_file
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(src.data)
        paths.append(p)
    out = workdir / "reference"
    report = build_session(paths, out, raw_root, recording_gap_s=recording_gap_s)
    return report, out


def reference_events(ref_out: Path) -> pd.DataFrame:
    parts = []
    for m in MESSAGES:
        for f in sorted((ref_out / "signals" / m.key).rglob("*.parquet")):
            t = pd.read_parquet(f)
            source_file = f"{f.parent.name}/{f.stem}.csv"
            for s in m.signals:
                parts.append(
                    pd.DataFrame(
                        {
                            "source_file": source_file,
                            "source_line": t["source_line"].astype("int64"),
                            "signal_name": s.name,
                            "spn": s.spn,
                            "event_timestamp": t["timestamp"],
                            "can_id": t["can_id"].astype("int64"),
                            "pgn": t["pgn"].astype("int64"),
                            "priority": t["priority"].astype("int64"),
                            "source_address": t["source_address"].astype("int64"),
                            "signal_raw": t[f"{s.name}_raw"].astype("Int64"),
                            "signal_value": t[s.name].astype("float64"),
                            "signal_status": t[f"{s.name}_status"].astype(str),
                        }
                    )
                )
    return pd.concat(parts, ignore_index=True)


def compare_events(ref: pd.DataFrame, silver: pd.DataFrame) -> dict:
    key = ["source_file", "source_line", "signal_name"]
    m = ref.merge(silver, on=key, how="outer", suffixes=("_ref", "_silver"), indicator=True)
    both = m[m["_merge"] == "both"]
    checks = {}
    for col in ("event_timestamp", "can_id", "pgn", "priority", "source_address", "spn", "signal_status"):
        a, b = both[f"{col}_silver"], both[f"{col}_ref"]
        if col in ("can_id", "pgn", "priority", "source_address", "spn"):
            a, b = a.astype("int64"), b.astype("int64")
        checks[col] = int((a.to_numpy() != b.to_numpy()).sum())
    raw_s, raw_r = both["signal_raw_silver"].astype("Int64"), both["signal_raw_ref"].astype("Int64")
    checks["signal_raw"] = int((~((raw_s == raw_r).fillna(raw_s.isna() & raw_r.isna()))).sum())
    checks["signal_value"] = int((~_close(both["signal_value_silver"], both["signal_value_ref"])).sum())
    checks["value_null_vs_not_valid"] = int(
        (both["signal_value_silver"].isna() != (both["signal_status_silver"] != "valid")).sum()
    )
    per_signal = (
        m.groupby(["signal_name", "_merge"], observed=True).size().unstack(fill_value=0).to_dict(orient="index")
    )
    max_abs = float(np.nanmax(np.abs(both["signal_value_silver"].astype(float) - both["signal_value_ref"].astype(float)))) \
        if both["signal_value_ref"].notna().any() else 0.0
    return {
        "reference_events": int((m["_merge"] != "right_only").sum()),
        "silver_events": int((m["_merge"] != "left_only").sum()),
        "only_in_reference": int((m["_merge"] == "left_only").sum()),
        "only_in_silver": int((m["_merge"] == "right_only").sum()),
        "compared": int(len(both)),
        "mismatches": checks,
        "max_abs_value_difference": max_abs,
        "per_signal": {k: {kk: int(vv) for kk, vv in v.items()} for k, v in per_signal.items()},
        "status_counts_silver": silver["signal_status"].value_counts().to_dict(),
    }


def compare_sessions(ref_out: Path, silver: pd.DataFrame) -> tuple[dict, dict]:
    (rec,) = (ref_out / "recordings").iterdir()
    ref = pd.read_csv(rec / "recording_sessions.csv", parse_dates=["start", "end"])
    s = silver.sort_values("session_start").reset_index(drop=True)
    ok = (
        len(ref) == len(s)
        and (ref["start"].values == s["session_start"].values).all()
        and (ref["end"].values == s["session_end"].values).all()
        and (ref["frames"].to_numpy() == s["frame_count"].to_numpy()).all()
    )
    mapping = dict(zip(ref["start"], s["recording_session_id"])) if len(ref) == len(s) else {}
    return {"reference_sessions": int(len(ref)), "silver_sessions": int(len(s)), "identical": bool(ok)}, mapping


def compare_telemetry(
    ref_out: Path, silver: pd.DataFrame, silver_sessions: pd.DataFrame, start_to_sid: dict, ref_events: pd.DataFrame
) -> dict:
    """Per session: reference grid must be contained in the Silver grid; every
    cell on the shared grid points must match. Silver rows outside the
    reference's (truncated) grid are counted separately - an intentional
    contract difference, not a parity failure."""
    results = []
    totals = {"reference_rows": 0, "silver_rows": 0, "overlap_rows": 0, "silver_only_rows": 0, "cells": 0,
              "value_mismatches": 0, "status_mismatches": 0, "age_mismatches": 0,
              "null_position_mismatches": 0, "freshness_inconsistent": 0, "silver_grid_violations": 0}
    bounds = silver_sessions.set_index("recording_session_id")
    # Reference status of the frame each reference cell used: (signal, frame timestamp) -> status.
    ref_status = ref_events.drop_duplicates(["signal_name", "event_timestamp"], keep="last").set_index(
        ["signal_name", "event_timestamp"]
    )["signal_status"]
    for d in sorted((ref_out / "sessions").iterdir()):
        rep = json.loads((d / "session_report.json").read_text())
        sid = start_to_sid.get(pd.Timestamp(rep["first_timestamp"]))
        ref = pd.read_parquet(d / "telemetry.parquet").rename(columns={"timestamp": "grid_timestamp"})
        sil = silver[silver["recording_session_id"] == sid].sort_values("grid_timestamp").reset_index(drop=True)
        r = {"reference_session": d.name, "silver_session": sid, "reference_rows": int(len(ref)),
             "silver_rows": int(len(sil))}
        # Silver grid contract: every 100 ms from ceil(session_start) to floor(session_end).
        b = bounds.loc[sid]
        expected = pd.date_range(pd.Timestamp(b["session_start"]).ceil("100ms"),
                                 pd.Timestamp(b["session_end"]).floor("100ms"), freq="100ms")
        r["silver_grid_matches_session"] = bool(len(expected) == len(sil) and (expected.values == sil["grid_timestamp"].values).all())
        totals["silver_grid_violations"] += int(not r["silver_grid_matches_session"])
        m = ref.merge(sil, on="grid_timestamp", how="left", suffixes=("_ref", ""), indicator=True)
        r["reference_grid_contained"] = bool((m["_merge"] == "both").all())
        both = m[m["_merge"] == "both"]
        r["overlap_rows"] = int(len(both))
        r["silver_only_rows_before"] = int((sil["grid_timestamp"] < ref["grid_timestamp"].min()).sum()) if len(ref) else len(sil)
        r["silver_only_rows_after"] = int((sil["grid_timestamp"] > ref["grid_timestamp"].max()).sum()) if len(ref) else 0
        vm = sm = am = nm = cells = 0
        for msg in MESSAGES:
            age = f"{msg.key}_age_ms"
            if age not in ref:
                continue
            ref_has = both[f"{age}_ref"].notna().to_numpy()
            am += int((ref_has != both[age].notna().to_numpy()).sum())
            used = both.loc[ref_has, "grid_timestamp"] - pd.to_timedelta(
                np.round(both.loc[ref_has, f"{age}_ref"].to_numpy() * 1000).astype("int64"), unit="us")
            am += int((~_close(both.loc[ref_has, age], both.loc[ref_has, f"{age}_ref"], AGE_TOL_MS, 0)).sum())
            for s in msg.signals:
                a, b_ = both[s.name], both[f"{s.name}_ref"]
                nm += int((a.isna().to_numpy() != b_.isna().to_numpy()).sum())
                vm += int((~_close(a, b_)).sum())
                expected = ref_status.reindex(pd.MultiIndex.from_arrays([[s.name] * len(used), used])).to_numpy()
                sm += int((both.loc[ref_has, f"{s.name}_status"].to_numpy() != expected).sum())
                cells += len(b_)
        for msg in MESSAGES:
            totals["freshness_inconsistent"] += int(
                ((sil[f"{msg.key}_freshness"] == "fresh").to_numpy() != sil[f"{msg.key}_age_ms"].notna().to_numpy()).sum()
            )
        r.update(cells=cells, value_mismatches=vm, status_mismatches=sm, age_mismatches=am, null_position_mismatches=nm)
        for k_tot, k in [("reference_rows", "reference_rows"), ("silver_rows", "silver_rows"), ("overlap_rows", "overlap_rows"),
                         ("cells", "cells"), ("value_mismatches", "value_mismatches"),
                         ("status_mismatches", "status_mismatches"), ("age_mismatches", "age_mismatches"),
                         ("null_position_mismatches", "null_position_mismatches")]:
            totals[k_tot] += r[k]
        totals["silver_only_rows"] += r["silver_only_rows_before"] + r["silver_only_rows_after"]
        results.append(r)
    totals["sessions_compared"] = len(results)
    totals["all_reference_grids_contained"] = all(r["reference_grid_contained"] for r in results)
    return {"totals": totals, "per_session": results}


def run(archive: Path, members: list[str], silver_root: Path, recording_gap_s: float, workdir: Path) -> dict:
    ref_report, ref_out = run_reference(archive, members, workdir, recording_gap_s)
    silver_events = _read(silver_root, EVENTS)
    silver_events["event_timestamp"] = silver_events["event_timestamp"].astype("datetime64[us]")
    ref_events = reference_events(ref_out)
    events = compare_events(ref_events, silver_events)
    sessions, mapping = compare_sessions(ref_out, _read(silver_root, SESSIONS))
    tele = _read(silver_root, TELEMETRY_TABLE)
    tele["grid_timestamp"] = tele["grid_timestamp"].astype("datetime64[us]")
    silver_sessions = _read(silver_root, SESSIONS)
    telemetry = compare_telemetry(ref_out, tele, silver_sessions, mapping, ref_events)
    t = telemetry["totals"]
    decode_ok = (
        events["only_in_reference"] == 0
        and events["only_in_silver"] == 0
        and not any(events["mismatches"].values())
    )
    alignment_ok = (
        t["all_reference_grids_contained"]
        and t["value_mismatches"] == 0
        and t["status_mismatches"] == 0
        and t["age_mismatches"] == 0
        and t["null_position_mismatches"] == 0
        and t["freshness_inconsistent"] == 0
        and t["silver_grid_violations"] == 0
    )
    ok = decode_ok and sessions["identical"] and alignment_ok
    return {
        "parity": bool(ok),
        "decode_parity_exact": bool(decode_ok),
        "session_parity_exact": bool(sessions["identical"]),
        "alignment_parity_on_shared_grid": bool(alignment_ok),
        "intentional_differences": {
            "grid_lifetime": "Silver grid spans the whole recording session; the reference grid spans only "
            "ceil(latest first observation)..floor(earliest last observation) of the messages present. "
            f"Silver-only rows: {t['silver_only_rows']}",
        },
        "tolerance": {"value_abs": ABS_TOL, "value_rel": REL_TOL, "age_ms": AGE_TOL_MS},
        "recording_gap_s": recording_gap_s,
        "signal_events": events,
        "recording_sessions": sessions,
        "telemetry": telemetry,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--include-list", type=Path, required=True)
    parser.add_argument("--silver-root", type=Path, default=Path("data/silver"))
    parser.add_argument("--recording-gap-s", type=float, default=DEFAULT_RECORDING_GAP_S)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    with tempfile.TemporaryDirectory() as tmp:
        report = run(args.archive, read_member_list(args.include_list), args.silver_root, args.recording_gap_s, Path(tmp))
    out = args.out or args.silver_root / "_parity" / "reference_parity.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    summary = {k: report[k] for k in ("parity", "decode_parity_exact", "session_parity_exact",
                                      "alignment_parity_on_shared_grid", "intentional_differences",
                                      "tolerance", "recording_sessions")}
    summary["signal_events"] = {k: v for k, v in report["signal_events"].items() if k not in ("per_signal",)}
    summary["telemetry"] = report["telemetry"]["totals"]
    print(json.dumps(summary, indent=2, default=str))
    return 0 if report["parity"] else 1


if __name__ == "__main__":
    sys.exit(main())
