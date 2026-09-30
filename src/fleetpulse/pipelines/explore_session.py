"""Exploratory analysis of one telemetry session: derived states, stats, plots.

Usage::

    python -m fleetpulse.pipelines.explore_session \\
        data/processed/sessions/part_3_20210119T100606_102050

Reads ``telemetry.parquet`` and writes, in the same session directory::

    analysis/states.parquet        timestamp + derived state columns (not telemetry)
    analysis/motion_episodes.csv   contiguous motion-state episodes
    analysis/summary.json          thresholds, time shares, per-state stats,
                                   threshold sensitivity grid
    analysis/fuel_summary.json     totals, fuel by state, idle, stops, coasting,
                                   braking, temperatures (analysis.fuel)
    analysis/fuel_*.csv            detail tables (stops, coasting episodes, ...)

and figures to ``reports/<session_id>/figures/``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from fleetpulse.analysis import fuel, fuel_plots, plots
from fleetpulse.analysis.operating_states import (
    StateThresholds,
    episodes,
    grid_period_s,
    label_states,
    summarise,
)

log = logging.getLogger("fleetpulse.explore")

SENSITIVITY_GRID = [
    (smooth, span, thr)
    for smooth, span in [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
    for thr in (0.1, 0.15, 0.2)
]


def sensitivity(telemetry: pd.DataFrame, base: StateThresholds) -> list[dict]:
    dt = grid_period_s(telemetry)
    rows = []
    for smooth, span, thr in SENSITIVITY_GRID:
        th = replace(base, smoothing_window_s=smooth, derivative_span_s=span, accel_threshold_mps2=thr)
        states = label_states(telemetry, th)
        eps = episodes(states, "motion_state", dt)
        share = states["motion_state"].astype(str).value_counts(normalize=True).mul(100).round(2)
        rows.append(
            {
                "smoothing_window_s": smooth,
                "derivative_span_s": span,
                "accel_threshold_mps2": thr,
                **{f"share_pct_{k}": float(v) for k, v in share.items()},
                **{
                    f"episodes_{k}": int(v)
                    for k, v in eps["state"].value_counts().items()
                    if k in ("accelerating", "cruising", "decelerating")
                },
            }
        )
    return rows


def load_message_table(session_dir: Path, key: str) -> pd.DataFrame:
    """Concatenate the per-file decoded table of one message for a session,
    clipped to the recording session's time bounds (a file can contribute to
    more than one recording session)."""
    manifest = pd.read_csv(session_dir / "raw_manifest.csv")
    report = json.loads((session_dir / "session_report.json").read_text())
    start, end = pd.Timestamp(report["first_timestamp"]), pd.Timestamp(report["last_timestamp"])
    signals_root = session_dir.parents[1] / "signals" / key
    parts = []
    for path in manifest["path"].drop_duplicates():
        rel = Path(path).parent.name + "/" + Path(path).stem + ".parquet"
        parts.append(pd.read_parquet(signals_root / rel))
    table = pd.concat(parts, ignore_index=True).sort_values("timestamp", ignore_index=True)
    return table[(table["timestamp"] >= start) & (table["timestamp"] <= end)].reset_index(drop=True)


def explore(session_dir: Path, reports_root: Path, th: StateThresholds = StateThresholds()) -> dict:
    telemetry = pd.read_parquet(session_dir / "telemetry.parquet")
    states = label_states(telemetry, th)
    summary = summarise(telemetry, states, th)
    summary["sensitivity"] = sensitivity(telemetry, th)

    out = session_dir / "analysis"
    out.mkdir(exist_ok=True)
    states.to_parquet(out / "states.parquet", index=False)
    episodes(states, "motion_state", grid_period_s(telemetry)).to_csv(out / "motion_episodes.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    tables = {k: load_message_table(session_dir, k) for k in ("hrlfc", "vdhr")}
    fuel_summary, details = fuel.analyse(telemetry, states, tables, th)
    (out / "fuel_summary.json").write_text(json.dumps(fuel_summary, indent=2, default=str))
    for name, table in details.items():
        if name != "coasting_samples":
            fname = name if name.startswith("fuel_") else f"fuel_{name}"
            table.to_csv(out / f"{fname}.csv", index=False, float_format="%.4f")

    fig_dir = reports_root / session_dir.name / "figures"
    figures = plots.make_all(telemetry, states, fig_dir)
    figures += fuel_plots.make_all(
        telemetry, details, details["coasting_samples"], tables["hrlfc"], fuel_summary, fig_dir
    )
    for f in figures:
        log.info("Figure %s", f)
    log.info("Analysis written to %s", out)
    summary["fuel"] = fuel_summary
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--reports-root", type=Path, default=Path("reports"))
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    explore(args.session_dir, args.reports_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
