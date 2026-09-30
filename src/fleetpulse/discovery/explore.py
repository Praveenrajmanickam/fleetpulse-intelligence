"""Business discovery v1: exploratory analysis of Silver telemetry (no Gold, no ML).

    nice -n 19 python -m fleetpulse.discovery.explore --silver-root data/discovery/silver

Input: validated Silver tables (Parquet) built by bronze_ingest + silver_build
from config/discovery_v1.txt. Reuses the validated analysis functions
(analysis.operating_states, analysis.fuel) per recording session, so nothing
is computed across a session boundary.

All thresholds below are DATASET-SPECIFIC EXPLORATORY DEFINITIONS, not
universal rules. Fuel is "observed fuel" (LFE SPN 183 integrated on the
100 ms grid, only where the frame is fresh and valid) and is reconciled per
session against the HRLFC SPN 5054 counter. Nothing here is labelled waste.

Outputs: data/discovery/analysis/{findings.json, episodes/*.csv, per_row.parquet}
and figures in reports/discovery_v1/figures/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds

from fleetpulse.analysis import fuel as fuelmod
from fleetpulse.analysis.operating_states import StateThresholds, episodes, label_states

DT = 0.1  # s, Silver grid
MIN_TARGET_SESSION_S = 600  # sessions shorter than this are edge fragments of the selected windows

# --- exploratory definitions (dataset-specific) --------------------------------
SPEED_BINS = [-0.1, 0.5, 5, 30, 60, 200]
SPEED_LABELS = ["stationary (<0.5)", "creeping (0.5-5)", "low (5-30)", "medium (30-60)", "higher (>=60)"]
RPM_BINS = [-1, 300, 700, 1000, 1300, 1600, 3000]
RPM_LABELS = ["off (<300)", "idle band (300-700)", "700-1000", "1000-1300", "1300-1600", ">=1600"]
LOAD_BINS = [-0.1, 0, 10, 25, 50, 75, 126]
LOAD_LABELS = ["0", "1-10", "11-25", "26-50", "51-75", "76-125"]
PEDAL_BINS = [-0.1, 0, 20, 50, 101]
PEDAL_LABELS = ["0", "1-20", "21-50", ">50"]
FUEL_BINS = [-0.1, 0, 3, 10, 30, 200]
FUEL_LABELS = ["0", "0-3", "3-10", "10-30", ">30"]
COOLANT_BINS = [-50, 40, 60, 75, 200]
COOLANT_LABELS = ["<40", "40-60", "60-75", ">=75"]

ENGINE_ON_RPM = 300.0
STATIONARY_KPH = 0.5
HIGH_LOAD_PCT = 75.0  # "high engine load" episode: load >= 75 % (top of the FMS 0-125 % range)
HIGH_FUEL_LPH = 30.0  # "high fuel-rate" episode
HARD_ACCEL_MPS2 = 0.5  # exploratory acceleration-event threshold (2 s smoothed wheel-speed derivative)
MIN_EVENT_S = 2.0
LONG_STATIONARY_S = 300.0
WARM_COOLANT_C = 70.0

TH = StateThresholds()


def load(silver_root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    tele = ds.dataset(silver_root / "silver_telemetry", partitioning="hive").to_table().to_pandas()
    sess = ds.dataset(silver_root / "silver_recording_sessions").to_table().to_pandas()
    counters = ds.dataset(silver_root / "silver_signal_events", partitioning="hive").to_table(
        filter=pc.field("message").isin(["HRLFC", "VDHR"]) & (pc.field("signal_status") == "valid"),
        columns=["recording_session_id", "event_timestamp", "signal_name", "signal_value"],
    ).to_pandas()
    return tele, sess, counters


def per_session_frame(t: pd.DataFrame) -> pd.DataFrame:
    """Telemetry of ONE session in the column layout the reference analysis functions expect."""
    df = t.sort_values("grid_timestamp").rename(columns={"grid_timestamp": "timestamp"}).reset_index(drop=True)
    df["timestamp"] = df["timestamp"].astype("datetime64[us]")
    return fuelmod.add_fuel_columns(df)


def label_row_regimes(df: pd.DataFrame, states: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["motion"] = states["motion_state"].astype(str).replace({"cruising": "steady_speed"})
    out["accel_mps2"] = states["acceleration_mps2"]
    out["speed_band"] = pd.cut(df["wheel_speed_kph"], SPEED_BINS, labels=SPEED_LABELS)
    out["rpm_band"] = pd.cut(df["engine_speed_rpm"], RPM_BINS, labels=RPM_LABELS)
    out["load_band"] = pd.cut(df["engine_load_pct"], LOAD_BINS, labels=LOAD_LABELS)
    out["pedal_band"] = pd.cut(df["accelerator_pedal_pct"], PEDAL_BINS, labels=PEDAL_LABELS)
    out["fuel_band"] = pd.cut(df["fuel_rate_lph"], FUEL_BINS, labels=FUEL_LABELS)
    out["coolant_band"] = pd.cut(df["coolant_temp_c"], COOLANT_BINS, labels=COOLANT_LABELS)
    on = df["engine_speed_rpm"] >= ENGINE_ON_RPM
    stationary = df["wheel_speed_kph"] < STATIONARY_KPH
    regime = np.select(
        [df["engine_speed_rpm"].isna() | df["wheel_speed_kph"].isna(), ~on, stationary,
         df["wheel_speed_kph"] < TH.creep_max_kph, out["motion"] == "accelerating",
         out["motion"] == "decelerating"],
        ["unknown", "engine_off", "stationary_engine_on", "creeping", "accelerating", "decelerating"],
        default="steady_speed",
    )
    out["regime"] = regime
    return out


def run_episodes(df: pd.DataFrame, mask: pd.Series, min_s: float = MIN_EVENT_S) -> pd.DataFrame:
    """Contiguous runs where ``mask`` is true, with duration, fuel, distance and signal summaries."""
    key = (mask != mask.shift()).cumsum()
    rows = []
    for _, g in df[mask].groupby(key[mask]):
        dur = len(g) * DT
        if dur < min_s:
            continue
        rows.append({
            "start": g["timestamp"].iloc[0], "end": g["timestamp"].iloc[-1], "duration_s": round(dur, 1),
            "fuel_l": g["fuel_l"].sum(), "distance_km": g["distance_km"].sum(),
            "speed_start_kph": g["wheel_speed_kph"].iloc[0], "speed_end_kph": g["wheel_speed_kph"].iloc[-1],
            "mean_speed_kph": g["wheel_speed_kph"].mean(), "mean_rpm": g["engine_speed_rpm"].mean(),
            "mean_load_pct": g["engine_load_pct"].mean(), "mean_pedal_pct": g["accelerator_pedal_pct"].mean(),
            "mean_fuel_rate_lph": g["fuel_rate_lph"].mean(), "max_fuel_rate_lph": g["fuel_rate_lph"].max(),
            "coolant_start_c": g["coolant_temp_c"].iloc[0], "coolant_end_c": g["coolant_temp_c"].iloc[-1],
        })
    return pd.DataFrame(rows)


def ep_summary(e: pd.DataFrame, fuel_total: float) -> dict:
    if e.empty:
        return {"count": 0}
    return {
        "count": int(len(e)), "total_min": round(e["duration_s"].sum() / 60, 1),
        "median_s": round(float(e["duration_s"].median()), 1), "max_s": round(float(e["duration_s"].max()), 1),
        "fuel_l": round(float(e["fuel_l"].sum()), 3), "fuel_share_pct": round(100 * float(e["fuel_l"].sum()) / fuel_total, 1),
        "distance_km": round(float(e["distance_km"].sum()), 2),
    }


def counter_check(counters: pd.DataFrame, sid: str) -> dict:
    c = counters[counters["recording_session_id"] == sid].sort_values("event_timestamp")
    out = {}
    for name, key, scale in (("total_fuel_used_l", "fuel_l_counter", 1.0), ("total_distance_m", "distance_km_counter", 1e-3)):
        v = c.loc[c["signal_name"] == name, "signal_value"]
        out[key] = round(float(v.iloc[-1] - v.iloc[0]) * scale, 3) if len(v) > 1 else None
    return out


def share_table(regimes: pd.DataFrame, df: pd.DataFrame, col: str, labels: list[str]) -> list[dict]:
    g = df.assign(band=regimes[col]).groupby("band", observed=False)
    time_s = g.size() * DT
    fuel_l = g["fuel_l"].sum()
    return [{"band": b, "time_min": round(float(time_s.get(b, 0)) / 60, 1),
             "time_pct": round(100 * float(time_s.get(b, 0)) / float(time_s.sum()), 1),
             "fuel_l": round(float(fuel_l.get(b, 0)), 2),
             "fuel_pct": round(100 * float(fuel_l.get(b, 0)) / max(float(fuel_l.sum()), 1e-9), 1)} for b in labels]


def analyse(silver_root: Path, out_dir: Path, fig_dir: Path) -> dict:
    tele, sess, counters = load(silver_root)
    sess = sess.sort_values("session_start").reset_index(drop=True)
    targets = sess[sess["duration_s"] >= MIN_TARGET_SESSION_S]
    fragments = sess[sess["duration_s"] < MIN_TARGET_SESSION_S]

    frames, regimes, all_eps = [], [], {k: [] for k in (
        "engine_on_stationary", "long_engine_on_stationary", "creeping", "high_load", "high_fuel_rate",
        "acceleration", "braking", "coasting", "cruise_control", "warm_up_idle")}
    session_rows = []
    for _, s in targets.iterrows():
        sid = s["recording_session_id"]
        df = per_session_frame(tele[tele["recording_session_id"] == sid])
        states = label_states(df, TH)
        r = label_row_regimes(df, states)
        on = df["engine_speed_rpm"] >= ENGINE_ON_RPM
        stat_on = on & (df["wheel_speed_kph"] < STATIONARY_KPH)
        warm = df["coolant_temp_c"] >= WARM_COOLANT_C
        ep = {
            "engine_on_stationary": run_episodes(df, stat_on),
            "long_engine_on_stationary": run_episodes(df, stat_on, LONG_STATIONARY_S),
            "creeping": run_episodes(df, r["regime"] == "creeping"),
            "high_load": run_episodes(df, df["engine_load_pct"] >= HIGH_LOAD_PCT),
            "high_fuel_rate": run_episodes(df, df["fuel_rate_lph"] >= HIGH_FUEL_LPH),
            "acceleration": run_episodes(df, r["accel_mps2"] >= HARD_ACCEL_MPS2),
            "cruise_control": run_episodes(df, df["cruise_control_active"] == 1),
            "warm_up_idle": run_episodes(df, stat_on & ~warm & df["coolant_temp_c"].notna()),
        }
        brakes = fuelmod.brake_applications(df, states, DT, TH)
        ep["braking"] = brakes.assign(fuel_l=0.0, distance_km=0.0)
        _, coast = fuelmod.coasting(df, states, DT)
        ep["coasting"] = coast
        tag = s["first_source_file"]
        for k, v in ep.items():
            if len(v):
                all_eps[k].append(v.assign(recording_session_id=sid))
        first_warm = df.loc[warm, "timestamp"].min()
        session_rows.append({
            "recording_session_id": sid, "start": str(s["session_start"]), "end": str(s["session_end"]),
            "duration_min": round(s["duration_s"] / 60, 1), "frames": int(s["frame_count"]),
            # relative to the analysed subset, not the archive (see gap audit for archive-level gaps)
            "gap_before_h_in_subset": round((s["gap_before_s"] or 0) / 3600, 1), "grid_rows": int(len(df)),
            "distance_km_integrated": round(float(df["distance_km"].sum()), 2),
            "fuel_l_integrated": round(float(df["fuel_l"].sum()), 3), **counter_check(counters, sid),
            "max_speed_kph": float(df["wheel_speed_kph"].max()),
            "coolant_start_c": float(df["coolant_temp_c"].dropna().iloc[0]),
            "coolant_max_c": float(df["coolant_temp_c"].max()),
            "minutes_to_70c": round((first_warm - df["timestamp"].iloc[0]).total_seconds() / 60, 1)
            if pd.notna(first_warm) else None,
            "ambient_min_c": float(df["ambient_temp_c"].min()), "ambient_max_c": float(df["ambient_temp_c"].max()),
            "first_file": tag,
        })
        frames.append(df.assign(recording_session_id=sid))
        regimes.append(r)
    df = pd.concat(frames, ignore_index=True)
    rg = pd.concat(regimes, ignore_index=True)
    eps = {k: (pd.concat(v, ignore_index=True) if v else pd.DataFrame()) for k, v in all_eps.items()}
    total_fuel = float(df["fuel_l"].sum())

    (out_dir / "episodes").mkdir(parents=True, exist_ok=True)
    for k, v in eps.items():
        v.to_csv(out_dir / "episodes" / f"{k}.csv", index=False, float_format="%.4f")
    pd.concat([df, rg.drop(columns=[])], axis=1).to_parquet(out_dir / "per_row.parquet", index=False)

    moving = df["wheel_speed_kph"] >= STATIONARY_KPH
    on = df["engine_speed_rpm"] >= ENGINE_ON_RPM
    rel_cols = ["engine_load_pct", "engine_speed_rpm", "accelerator_pedal_pct", "wheel_speed_kph"]
    findings = {
        "exploratory_definitions": {
            "speed_bins_kph": SPEED_BINS, "rpm_bins": RPM_BINS, "load_bins_pct": LOAD_BINS, "pedal_bins_pct": PEDAL_BINS,
            "fuel_rate_bins_lph": FUEL_BINS, "coolant_bins_c": COOLANT_BINS, "engine_on_rpm": ENGINE_ON_RPM,
            "stationary_kph": STATIONARY_KPH, "creep_max_kph": TH.creep_max_kph, "accel_threshold_mps2": TH.accel_threshold_mps2,
            "high_load_pct": HIGH_LOAD_PCT, "high_fuel_lph": HIGH_FUEL_LPH, "hard_accel_mps2": HARD_ACCEL_MPS2,
            "min_event_s": MIN_EVENT_S, "long_stationary_s": LONG_STATIONARY_S, "warm_coolant_c": WARM_COOLANT_C,
            "note": "dataset-specific exploratory definitions, not universal thresholds",
        },
        "coverage": {
            "target_sessions": len(targets), "fragment_sessions": len(fragments),
            "fragment_minutes": round(float(fragments["duration_s"].sum()) / 60, 1),
            "telemetry_rows_all": int(len(tele)), "telemetry_rows_targets": int(len(df)),
            "observed_hours": round(len(df) * DT / 3600, 2),
            "engine_on_hours": round(float(on.sum()) * DT / 3600, 2),
            "distance_km_integrated": round(float(df["distance_km"].sum()), 1),
            "fuel_l_integrated": round(total_fuel, 2),
            "sessions": session_rows,
        },
        "distributions": {
            "regime": share_table(rg, df, "regime", ["stationary_engine_on", "creeping", "accelerating", "steady_speed",
                                                     "decelerating", "engine_off", "unknown"]),
            "speed": share_table(rg, df, "speed_band", SPEED_LABELS),
            "rpm": share_table(rg, df, "rpm_band", RPM_LABELS),
            "load": share_table(rg, df, "load_band", LOAD_LABELS),
            "pedal": share_table(rg, df, "pedal_band", PEDAL_LABELS),
            "fuel_rate": share_table(rg, df, "fuel_band", FUEL_LABELS),
            "coolant": share_table(rg, df, "coolant_band", COOLANT_LABELS),
        },
        "episodes": {k: ep_summary(v, total_fuel) for k, v in eps.items()},
        "fuel_by_regime": share_table(rg, df, "regime", ["stationary_engine_on", "creeping", "accelerating",
                                                         "steady_speed", "decelerating"]),
        "relationships": {
            "spearman_all_engine_on": {c: round(fuelmod.spearman(df[on], c), 3) for c in rel_cols},
            "spearman_moving": {c: round(fuelmod.spearman(df[moving], c), 3) for c in rel_cols},
            "load_vs_speed_spearman_moving": round(float(df[moving][["engine_load_pct", "wheel_speed_kph"]].rank().corr().iloc[0, 1]), 3),
            "load_vs_pedal_spearman_moving": round(float(df[moving][["engine_load_pct", "accelerator_pedal_pct"]].rank().corr().iloc[0, 1]), 3),
            "fuel_per_load_by_rpm_band": _fuel_per_load(df[on & (df["engine_load_pct"] > 0)], rg[on & (df["engine_load_pct"] > 0)]),
            "idle_fuel_rate_by_coolant_band": _idle_by_coolant(df, rg, on),
        },
        "cruise_control": _cruise(df, rg),
    }
    (out_dir / "findings.json").write_text(json.dumps(findings, indent=2, default=str))
    from fleetpulse.discovery import figures

    figures.make_all(df, rg, eps, targets, fig_dir)
    return findings


def _fuel_per_load(df: pd.DataFrame, rg: pd.DataFrame) -> list[dict]:
    g = df.assign(rpm_band=rg["rpm_band"]).groupby("rpm_band", observed=True)
    return [{"rpm_band": b, "samples": int(len(x)),
             "median_fuel_lph_per_load_pct": round(float((x["fuel_rate_lph"] / x["engine_load_pct"]).median()), 3)}
            for b, x in g if len(x) > 50]


def _idle_by_coolant(df: pd.DataFrame, rg: pd.DataFrame, on: pd.Series) -> list[dict]:
    idle = on & (df["wheel_speed_kph"] < STATIONARY_KPH) & (df["accelerator_pedal_pct"] == 0)
    g = df[idle].assign(c=rg.loc[idle, "coolant_band"]).groupby("c", observed=True)
    return [{"coolant_band": b, "minutes": round(len(x) * DT / 60, 1), "median_fuel_lph": round(float(x["fuel_rate_lph"].median()), 2),
             "median_rpm": round(float(x["engine_speed_rpm"].median()), 1)} for b, x in g]


def _cruise(df: pd.DataFrame, rg: pd.DataFrame) -> dict:
    moving = df["wheel_speed_kph"] >= 60
    rows = []
    for state, name in ((1, "cruise_on"), (0, "cruise_off")):
        m = moving & (df["cruise_control_active"] == state)
        x = df[m]
        rows.append({"state": name, "minutes_at_>=60kph": round(len(x) * DT / 60, 1),
                     "distance_km": round(float(x["distance_km"].sum()), 2), "fuel_l": round(float(x["fuel_l"].sum()), 3),
                     "l_per_100km": round(100 * float(x["fuel_l"].sum()) / float(x["distance_km"].sum()), 1) if x["distance_km"].sum() > 1 else None,
                     "speed_std_kph": round(float(x["wheel_speed_kph"].std()), 2) if len(x) else None,
                     "mean_speed_kph": round(float(x["wheel_speed_kph"].mean()), 1) if len(x) else None})
    sessions_with_cruise = df.loc[df["cruise_control_active"] == 1, "recording_session_id"].nunique()
    return {"at_or_above_60kph": rows, "sessions_with_cruise": int(sessions_with_cruise),
            "note": "comparison is observational: speed, grade, traffic and load differ between cruise and non-cruise periods"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--silver-root", type=Path, default=Path("data/discovery/silver"))
    parser.add_argument("--out", type=Path, default=Path("data/discovery/analysis"))
    parser.add_argument("--figures", type=Path, default=Path("reports/discovery_v1/figures"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    f = analyse(args.silver_root, args.out, args.figures)
    print(json.dumps({k: f[k] for k in ("coverage", "episodes")}, indent=1, default=str)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
