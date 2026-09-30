"""Fuel, stop, coasting and braking analysis for one telemetry session.

Fuel accounting
---------------
* Session totals come from the cumulative counters: HRLFC SPN 5054 (fuel) and
  VDHR SPN 917 (distance), as last - first of the decoded frames.
* Attribution to states, stops and zones integrates LFE SPN 183 fuel rate on
  the telemetry grid: ``fuel_l = fuel_rate_lph * grid_period_s / 3600``.
  The HRLFC counter only moves in ~0.01 L steps at 1 Hz, too coarse to split
  a few seconds of driving. The integrated rate is reconciled against the
  counter and the ratio is reported.
* FMS: fuel rate and fuel economy are "indications, not contractual values".

Nothing here judges whether fuel use is good or bad.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fleetpulse.analysis.operating_states import StateThresholds, episodes, grid_period_s

RPM_BINS = [0, 700, 900, 1100, 1300, 1500, 1800]
RPM_LABELS = ["<700", "700-900", "900-1100", "1100-1300", "1300-1500", "1500-1800"]
LOAD_BINS = [-0.1, 0, 10, 25, 50, 75, 125]
LOAD_LABELS = ["0", "1-10", "11-25", "26-50", "51-75", "76-125"]

ENGINE_IDLE_MAX_RPM = 700.0  # stationary median 600 rpm, p95 629 rpm in this session
COAST_MIN_SPEED_KPH = 5.0
MIN_EPISODE_S = 2.0

STOP_COLUMNS = ["start", "end", "duration_s", "fuel_l", "mean_fuel_rate_lph", "median_engine_speed_rpm",
                "brake_pressed_pct", "engine_running_pct"]
COAST_EXTRA_COLUMNS = ["speed_start_kph", "speed_end_kph", "median_engine_speed_rpm", "mean_fuel_rate_lph",
                       "zero_fuel_rate_pct", "median_engine_load_pct", "fuel_l", "distance_km"]
BRAKE_COLUMNS = ["start", "duration_s", "speed_start_kph", "speed_end_kph", "speed_drop_kph",
                 "min_acceleration_mps2", "mean_acceleration_mps2", "context"]


def counter_delta(table: pd.DataFrame, col: str) -> dict:
    s = table.dropna(subset=[col]).sort_values("timestamp")
    return {
        "first": float(s[col].iloc[0]),
        "last": float(s[col].iloc[-1]),
        "delta": float(s[col].iloc[-1] - s[col].iloc[0]),
        "first_time": str(s["timestamp"].iloc[0]),
        "last_time": str(s["timestamp"].iloc[-1]),
        "monotonic": bool(s[col].is_monotonic_increasing),
    }


def add_fuel_columns(telemetry: pd.DataFrame) -> pd.DataFrame:
    dt = grid_period_s(telemetry)
    return telemetry.assign(
        fuel_l=telemetry["fuel_rate_lph"] * dt / 3600,
        distance_km=telemetry["wheel_speed_kph"] * dt / 3600,
    )


def _group_fuel(df: pd.DataFrame, by, dt: float) -> pd.DataFrame:
    g = df.groupby(by, observed=True)
    out = pd.DataFrame(
        {
            "time_s": g.size() * dt,
            "fuel_l": g["fuel_l"].sum(),
            "distance_km": g["distance_km"].sum(),
            "mean_fuel_rate_lph": g["fuel_rate_lph"].mean(),
            "median_fuel_rate_lph": g["fuel_rate_lph"].median(),
        }
    )
    out["time_share_pct"] = 100 * out["time_s"] / out["time_s"].sum()
    out["fuel_share_pct"] = 100 * out["fuel_l"] / out["fuel_l"].sum()
    # Only meaningful where the truck actually covered distance.
    out["l_per_100km"] = np.where(out["distance_km"] >= 0.1, 100 * out["fuel_l"] / out["distance_km"], np.nan)
    return out


def fuel_by_state(df: pd.DataFrame, states: pd.DataFrame, dt: float) -> pd.DataFrame:
    return _group_fuel(df.assign(motion_state=states["motion_state"].astype(str)), "motion_state", dt)


def operating_zones(df: pd.DataFrame, dt: float) -> pd.DataFrame:
    """Time and fuel per engine-speed x engine-load bin (engine running only)."""
    run = df[df["engine_speed_rpm"] >= 300].assign(
        rpm_band=pd.cut(df["engine_speed_rpm"], RPM_BINS, labels=RPM_LABELS, right=False),
        load_band=pd.cut(df["engine_load_pct"], LOAD_BINS, labels=LOAD_LABELS),
    )
    return _group_fuel(run, ["rpm_band", "load_band"], dt).reset_index()


def binned_relationship(df: pd.DataFrame, x: str, bins) -> pd.DataFrame:
    b = pd.cut(df[x], bins)
    g = df.groupby(b, observed=True)["fuel_rate_lph"]
    return pd.DataFrame(
        {"samples": g.size(), "median_fuel_rate_lph": g.median(), "p10": g.quantile(0.1), "p90": g.quantile(0.9)}
    ).reset_index().assign(**{x: lambda d: d[x].astype(str)})


def spearman(df: pd.DataFrame, x: str, mask=None) -> float:
    d = df if mask is None else df[mask]
    return float(d[[x, "fuel_rate_lph"]].rank().corr().iloc[0, 1])


def stops(df: pd.DataFrame, states: pd.DataFrame, dt: float) -> pd.DataFrame:
    s = states.assign(stationary=states["motion_state"] == "stationary")
    eps = episodes(s, "stationary", dt)
    eps = eps[eps["state"] == "True"].copy()
    rows = []
    for _, e in eps.iterrows():
        m = (df["timestamp"] >= e["start"]) & (df["timestamp"] <= e["end"])
        rows.append(
            {
                "start": e["start"],
                "end": e["end"],
                "duration_s": e["duration_s"],
                "fuel_l": df.loc[m, "fuel_l"].sum(),
                "mean_fuel_rate_lph": df.loc[m, "fuel_rate_lph"].mean(),
                "median_engine_speed_rpm": df.loc[m, "engine_speed_rpm"].median(),
                "brake_pressed_pct": 100 * (df.loc[m, "brake_switch"] == 1).mean(),
                "engine_running_pct": 100 * (df.loc[m, "engine_speed_rpm"] >= 300).mean(),
            }
        )
    return pd.DataFrame(rows, columns=STOP_COLUMNS)


def coasting(df: pd.DataFrame, states: pd.DataFrame, dt: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Candidate coasting: speed decreasing (smoothed acceleration < 0), pedal 0,
    brake released, >= 5 km/h; only episodes >= MIN_EPISODE_S are kept.

    Split by engine speed: at idle (< ENGINE_IDLE_MAX_RPM) vs above idle. The
    split describes what the engine was doing; whether the driveline was
    engaged cannot be verified (ETC1 not decoded).
    """
    cand = (
        (states["acceleration_mps2"] < 0)
        & (df["accelerator_pedal_pct"] == 0)
        & (df["brake_switch"] == 0)
        & (df["wheel_speed_kph"] >= COAST_MIN_SPEED_KPH)
    )
    engine = np.where(df["engine_speed_rpm"] < ENGINE_IDLE_MAX_RPM, "engine_at_idle", "engine_above_idle")
    kind = pd.Series(np.where(cand, engine, "not_coasting"), index=df.index)
    samples = df[cand].assign(coast_kind=kind[cand], acceleration_mps2=states.loc[cand, "acceleration_mps2"])

    eps = episodes(pd.DataFrame({"timestamp": df["timestamp"], "kind": kind}), "kind", dt)
    eps = eps[(eps["state"] != "not_coasting") & (eps["duration_s"] >= MIN_EPISODE_S)].reset_index(drop=True)
    in_episode = pd.Series(False, index=df.index)
    for _, e in eps.iterrows():
        in_episode |= (df["timestamp"] >= e["start"]) & (df["timestamp"] <= e["end"])
    samples = samples[in_episode[cand].to_numpy()]
    extra = []
    for _, e in eps.iterrows():
        m = (df["timestamp"] >= e["start"]) & (df["timestamp"] <= e["end"])
        seg = df.loc[m]
        extra.append(
            {
                "speed_start_kph": seg["wheel_speed_kph"].iloc[0],
                "speed_end_kph": seg["wheel_speed_kph"].iloc[-1],
                "median_engine_speed_rpm": seg["engine_speed_rpm"].median(),
                "mean_fuel_rate_lph": seg["fuel_rate_lph"].mean(),
                "zero_fuel_rate_pct": 100 * (seg["fuel_rate_lph"] == 0).mean(),
                "median_engine_load_pct": seg["engine_load_pct"].median(),
                "fuel_l": seg["fuel_l"].sum(),
                "distance_km": seg["distance_km"].sum(),
            }
        )
    eps = pd.concat([eps.reset_index(drop=True), pd.DataFrame(extra, columns=COAST_EXTRA_COLUMNS)], axis=1)
    return samples, eps


def brake_applications(df: pd.DataFrame, states: pd.DataFrame, dt: float, th: StateThresholds) -> pd.DataFrame:
    s = states.assign(braking=states["braking"].fillna(False))
    eps = episodes(s, "braking", dt)
    eps = eps[eps["state"] == "True"].copy()
    rows = []
    for _, e in eps.iterrows():
        m = (df["timestamp"] >= e["start"]) & (df["timestamp"] <= e["end"])
        seg, acc = df.loc[m], states.loc[m, "acceleration_mps2"]
        v0, v1 = seg["wheel_speed_kph"].iloc[0], seg["wheel_speed_kph"].iloc[-1]
        rows.append(
            {
                "start": e["start"],
                "duration_s": e["duration_s"],
                "speed_start_kph": v0,
                "speed_end_kph": v1,
                "speed_drop_kph": v0 - v1,
                "min_acceleration_mps2": acc.min(),
                "mean_acceleration_mps2": acc.mean(),
                "context": "while stationary" if v0 < th.stationary_max_kph
                else "to standstill" if v1 < th.stationary_max_kph else "slowing",
            }
        )
    return pd.DataFrame(rows, columns=BRAKE_COLUMNS)


def _describe(x: pd.Series) -> dict:
    x = x.dropna()
    if x.empty:
        return {"n": 0}
    return {
        "n": int(len(x)),
        "min": round(float(x.min()), 3),
        "median": round(float(x.median()), 3),
        "mean": round(float(x.mean()), 3),
        "max": round(float(x.max()), 3),
        "first": round(float(x.iloc[0]), 3),
        "last": round(float(x.iloc[-1]), 3),
    }


def analyse(
    telemetry: pd.DataFrame,
    states: pd.DataFrame,
    tables: dict[str, pd.DataFrame],
    th: StateThresholds = StateThresholds(),
) -> tuple[dict, dict[str, pd.DataFrame]]:
    """Return (summary dict, detail tables)."""
    dt = grid_period_s(telemetry)
    df = add_fuel_columns(telemetry)

    fuel_ctr = counter_delta(tables["hrlfc"], "total_fuel_used_l")
    dist_ctr = counter_delta(tables["vdhr"], "total_distance_m")
    dist_km = dist_ctr["delta"] / 1000
    fuel_int = float(df["fuel_l"].sum())
    dist_int = float(df["distance_km"].sum())

    by_state = fuel_by_state(df, states, dt)
    zones = operating_zones(df, dt)
    stop_table = stops(df, states, dt)
    coast_samples, coast_eps = coasting(df, states, dt)
    brakes = brake_applications(df, states, dt, th)

    running = df["engine_speed_rpm"] >= 300
    stationary = states["motion_state"] == "stationary"
    idle_speed = running & (df["engine_speed_rpm"] < ENGINE_IDLE_MAX_RPM) & (df["accelerator_pedal_pct"] == 0)
    moving = df["wheel_speed_kph"] >= th.stationary_max_kph

    def fuel_block(mask):
        return {
            "time_s": round(float(mask.sum() * dt), 1),
            "fuel_l": round(float(df.loc[mask, "fuel_l"].sum()), 4),
            "fuel_share_pct": round(100 * float(df.loc[mask, "fuel_l"].sum()) / fuel_int, 2),
            "mean_fuel_rate_lph": round(float(df.loc[mask, "fuel_rate_lph"].mean()), 3),
        }

    decel = states["motion_state"] == "decelerating"
    braking = states["braking"].fillna(False).astype(bool)
    rels = {
        "engine_speed_rpm": [500, 700, 900, 1100, 1300, 1500, 1700],
        "engine_load_pct": [-0.1, 0, 10, 20, 30, 40, 60, 90],
        "wheel_speed_kph": [-0.1, 0.5, 5, 15, 25, 35, 45, 60],
        "accelerator_pedal_pct": [-0.1, 0, 10, 20, 30, 40, 60, 80],
    }
    summary = {
        "method": {
            "totals": "cumulative counters HRLFC SPN 5054 / VDHR SPN 917 (last - first)",
            "attribution": "LFE SPN 183 fuel rate integrated on the 100 ms telemetry grid",
            "fms_note": "fuel rate and fuel economy are indications, not contractual values",
            "engine_idle_max_rpm": ENGINE_IDLE_MAX_RPM,
            "coasting_definition": "smoothed acceleration < 0 & pedal == 0 & brake released & speed >= "
            f"{COAST_MIN_SPEED_KPH} km/h; episodes >= {MIN_EPISODE_S} s; split by engine speed "
            f"< {ENGINE_IDLE_MAX_RPM:g} rpm (engine at idle) vs above",
            "thresholds": th.to_dict(),
        },
        "totals": {
            "distance_km_counter": round(dist_km, 3),
            "distance_km_integrated_speed": round(dist_int, 3),
            "fuel_l_counter": round(fuel_ctr["delta"], 3),
            "fuel_l_integrated_rate": round(fuel_int, 3),
            "counter_to_integrated_fuel_ratio": round(fuel_ctr["delta"] / fuel_int, 4),
            "counter_to_integrated_distance_ratio": round(dist_km / dist_int, 4),
            "km_per_l": round(dist_km / fuel_ctr["delta"], 3),
            "l_per_100km": round(100 * fuel_ctr["delta"] / dist_km, 2),
            "counter_resolution_note": "HRLFC advances in ~0.01 L steps in this data",
            "odometer_km_start": round(dist_ctr["first"] / 1000, 3),
            "fuel_counter_l_start": round(fuel_ctr["first"], 3),
            "fuel_counter_span": [fuel_ctr["first_time"], fuel_ctr["last_time"]],
        },
        "fuel_by_motion_state": by_state.round(4).reset_index().to_dict(orient="records"),
        "idle": {
            "stationary_engine_running": fuel_block(stationary & running),
            "engine_at_idle_speed_pedal_0_any_motion": fuel_block(idle_speed),
            "engine_at_idle_speed_pedal_0_while_moving": fuel_block(idle_speed & moving),
        },
        "fuel_rate_relationships": {
            "spearman_rho_all_samples": {x: round(spearman(df, x), 3) for x in rels},
            "spearman_rho_moving_only": {x: round(spearman(df, x, moving), 3) for x in rels},
            "binned_median": {x: binned_relationship(df, x, b).round(3).to_dict(orient="records") for x, b in rels.items()},
            "zero_fuel_rate_pct": round(100 * float((df["fuel_rate_lph"] == 0).mean()), 2),
        },
        "operating_zones_top": zones.sort_values("time_s", ascending=False).head(8).round(3).to_dict(orient="records"),
        "stops": {
            "count": int(len(stop_table)),
            "count_ge_2s": int((stop_table["duration_s"] >= 2).sum()),
            "count_ge_10s": int((stop_table["duration_s"] >= 10).sum()),
            "total_time_s": round(float(stop_table["duration_s"].sum()), 1),
            "total_fuel_l": round(float(stop_table["fuel_l"].sum()), 4),
            "longest_s": float(stop_table["duration_s"].max()) if len(stop_table) else 0.0,
            "mean_fuel_rate_lph": round(float(df.loc[stationary, "fuel_rate_lph"].mean()), 3),
        },
        "coasting": {
            "sample_time_s": {k: round(float(v * dt), 1) for k, v in coast_samples["coast_kind"].value_counts().items()},
            "fuel_rate_lph": {k: _describe(g["fuel_rate_lph"]) for k, g in coast_samples.groupby("coast_kind")},
            "zero_fuel_rate_pct": {
                k: round(100 * float((g["fuel_rate_lph"] == 0).mean()), 2) for k, g in coast_samples.groupby("coast_kind")
            },
            "engine_load_pct": {k: _describe(g["engine_load_pct"]) for k, g in coast_samples.groupby("coast_kind")},
            "episodes_ge_2s": int(len(coast_eps)),
            "episode_fuel_l": {k: round(float(g["fuel_l"].sum()), 4) for k, g in coast_eps.groupby("state")},
            "episode_distance_km": {k: round(float(g["distance_km"].sum()), 3) for k, g in coast_eps.groupby("state")},
        },
        "braking": {
            "applications": int(len(brakes)),
            "by_context": brakes["context"].value_counts().to_dict(),
            "decelerating_time_s": round(float(decel.sum() * dt), 1),
            "decelerating_with_brake_pct": round(100 * float(braking[decel].mean()), 2) if decel.any() else None,
            "acceleration_mps2_decel_with_brake": _describe(states.loc[decel & braking, "acceleration_mps2"]),
            "acceleration_mps2_decel_without_brake": _describe(states.loc[decel & ~braking, "acceleration_mps2"]),
            "fuel_rate_lph_decel_with_brake": _describe(df.loc[decel & braking, "fuel_rate_lph"]),
            "fuel_rate_lph_decel_without_brake": _describe(df.loc[decel & ~braking, "fuel_rate_lph"]),
        },
        "context": {
            "coolant_temp_c": _describe(df["coolant_temp_c"]),
            "ambient_temp_c": _describe(df["ambient_temp_c"]),
        },
    }
    details = {
        "fuel_by_motion_state": by_state.reset_index(),
        "operating_zones": zones,
        "stops": stop_table,
        "coasting_episodes": coast_eps,
        "brake_applications": brakes,
        "coasting_samples": coast_samples,
    }
    return summary, details
