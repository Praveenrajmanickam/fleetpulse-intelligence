"""Exploratory operating-state labelling from verified telemetry.

Everything here is *derived* from the aligned telemetry table; nothing is
decoded. The thresholds are analyst choices for exploration, not standards,
and are kept together in :class:`StateThresholds` so they are reported with
every result.

States
------
engine_state   running (rpm >= running_min_rpm) | off (rpm == 0)
               | transitional (0 < rpm < running_min_rpm) | unknown (no rpm)
motion_state   stationary (< stationary_max_kph)
               | creeping (< creep_max_kph)
               | accelerating / decelerating (|a| > accel_threshold)
               | cruising (steady speed, |a| <= accel_threshold)
               | unknown (speed or acceleration missing)
braking        service-brake pedal switch (CCVS SPN 597) == depressed.
               Does not see retarder, engine brake or park brake.
demand_state   pedal_pressed (SPN 91 > 0) | zero_pedal_load (pedal 0 and
               SPN 92 == 0: FMS defines load as clipped to 0 during engine
               braking) | zero_pedal (pedal 0, load > 0) | unknown

"cruising" means steady wheel speed; it is unrelated to the cruise-control
signal (SPN 595).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

KPH_TO_MPS = 1 / 3.6


@dataclass(frozen=True)
class StateThresholds:
    running_min_rpm: float = 300.0
    stationary_max_kph: float = 0.5
    creep_max_kph: float = 5.0
    accel_threshold_mps2: float = 0.1
    smoothing_window_s: float = 2.0  # centred rolling mean on speed before differentiating
    derivative_span_s: float = 2.0  # central difference over +/- span/2

    def to_dict(self) -> dict:
        return asdict(self)


def grid_period_s(telemetry: pd.DataFrame) -> float:
    return float(telemetry["timestamp"].diff().dt.total_seconds().median())


def acceleration_mps2(telemetry: pd.DataFrame, th: StateThresholds) -> pd.Series:
    """Longitudinal acceleration from wheel speed (offline, centred window).

    Missing speed samples propagate as NaN instead of being bridged.
    """
    dt = grid_period_s(telemetry)
    speed = telemetry["wheel_speed_kph"] * KPH_TO_MPS
    win = int(round(th.smoothing_window_s / dt)) | 1  # odd
    smooth = speed.rolling(win, center=True, min_periods=win).mean()
    half = int(round(th.derivative_span_s / dt / 2))
    return (smooth.shift(-half) - smooth.shift(half)) / (2 * half * dt)


def label_states(telemetry: pd.DataFrame, th: StateThresholds = StateThresholds()) -> pd.DataFrame:
    """Return timestamp + derived columns; telemetry itself is not modified."""
    rpm = telemetry["engine_speed_rpm"]
    speed = telemetry["wheel_speed_kph"]
    accel = acceleration_mps2(telemetry, th)
    pedal = telemetry["accelerator_pedal_pct"]
    load = telemetry["engine_load_pct"]

    engine = np.select(
        [rpm.isna(), rpm >= th.running_min_rpm, rpm == 0],
        ["unknown", "running", "off"],
        default="transitional",
    )
    motion = np.select(
        [
            speed.isna(),
            speed < th.stationary_max_kph,
            speed < th.creep_max_kph,
            accel.isna(),
            accel > th.accel_threshold_mps2,
            accel < -th.accel_threshold_mps2,
        ],
        ["unknown", "stationary", "creeping", "unknown", "accelerating", "decelerating"],
        default="cruising",
    )
    demand = np.select(
        [pedal.isna() | load.isna(), pedal > 0, load == 0],
        ["unknown", "pedal_pressed", "zero_pedal_load"],
        default="zero_pedal",
    )
    brake = telemetry["brake_switch"].astype("boolean") if "brake_switch" in telemetry else pd.NA

    return pd.DataFrame(
        {
            "timestamp": telemetry["timestamp"],
            "acceleration_mps2": accel,
            "engine_state": pd.Categorical(engine),
            "motion_state": pd.Categorical(motion),
            "braking": brake,
            "demand_state": pd.Categorical(demand),
            # Engine speed per unit wheel speed; only meaningful when moving.
            "rpm_per_kph": (rpm / speed).where(speed >= th.creep_max_kph),
        }
    )


def episodes(states: pd.DataFrame, column: str, period_s: float) -> pd.DataFrame:
    """Run-length encode a state column into contiguous episodes."""
    s = states[column].astype(str)
    run_id = (s != s.shift()).cumsum()
    grouped = states.groupby(run_id)
    return pd.DataFrame(
        {
            "state": grouped[column].first().astype(str),
            "start": grouped["timestamp"].first(),
            "end": grouped["timestamp"].last(),
            "duration_s": grouped.size() * period_s,
        }
    ).reset_index(drop=True)


def summarise(telemetry: pd.DataFrame, states: pd.DataFrame, th: StateThresholds) -> dict:
    dt = grid_period_s(telemetry)
    speed = telemetry["wheel_speed_kph"]
    rpm = telemetry["engine_speed_rpm"]
    moving = speed >= th.stationary_max_kph

    def share(col):
        return {str(k): round(100 * v, 2) for k, v in states[col].value_counts(normalize=True).items()}

    def describe(x):
        x = x.dropna()
        if x.empty:
            return {"n": 0}
        q = x.quantile([0.05, 0.5, 0.95])
        return {
            "n": int(len(x)),
            "min": round(float(x.min()), 3),
            "p05": round(float(q[0.05]), 3),
            "median": round(float(q[0.5]), 3),
            "mean": round(float(x.mean()), 3),
            "p95": round(float(q[0.95]), 3),
            "max": round(float(x.max()), 3),
        }

    motion_eps = episodes(states, "motion_state", dt)
    brake_eps = episodes(states.assign(braking=states["braking"].fillna(False)), "braking", dt)
    brake_eps = brake_eps[brake_eps["state"] == "True"]

    per_state = {}
    for state, idx in states.groupby("motion_state", observed=True).groups.items():
        per_state[str(state)] = {
            "time_s": round(len(idx) * dt, 1),
            "speed_kph": describe(speed.loc[idx]),
            "engine_speed_rpm": describe(rpm.loc[idx]),
            "accelerator_pedal_pct": describe(telemetry.loc[idx, "accelerator_pedal_pct"]),
            "engine_load_pct": describe(telemetry.loc[idx, "engine_load_pct"]),
            "acceleration_mps2": describe(states.loc[idx, "acceleration_mps2"]),
            "braking_pct": round(100 * float(states.loc[idx, "braking"].fillna(False).mean()), 2),
        }

    accel = states["acceleration_mps2"]
    braking = states["braking"].fillna(False).astype(bool)
    decel = states["motion_state"] == "decelerating"
    return {
        "thresholds": th.to_dict(),
        "grid_period_s": dt,
        "duration_s": round(len(telemetry) * dt, 1),
        "distance_km_from_wheel_speed": round(float((speed.fillna(0) * dt / 3600).sum()), 3),
        "time_share_pct": {c: share(c) for c in ["engine_state", "motion_state", "demand_state"]},
        "braking_time_share_pct": round(100 * float(braking.mean()), 2),
        "moving_time_share_pct": round(100 * float(moving.mean()), 2),
        "signals": {
            "wheel_speed_kph": describe(speed),
            "engine_speed_rpm": describe(rpm),
            "engine_speed_rpm_stationary": describe(rpm[~moving]),
            "accelerator_pedal_pct": describe(telemetry["accelerator_pedal_pct"]),
            "engine_load_pct": describe(telemetry["engine_load_pct"]),
            "acceleration_mps2": describe(accel),
            "acceleration_mps2_braking": describe(accel[braking & moving]),
            "acceleration_mps2_not_braking_decelerating": describe(accel[decel & ~braking]),
        },
        "per_motion_state": per_state,
        "decelerating_with_brake_pct": round(100 * float(braking[decel].mean()), 2) if decel.any() else None,
        "motion_episodes": {
            str(k): {"count": int(len(g)), "median_s": float(g["duration_s"].median()), "max_s": float(g["duration_s"].max())}
            for k, g in motion_eps.groupby("state")
        },
        "brake_applications": {
            "count": int(len(brake_eps)),
            "median_s": float(brake_eps["duration_s"].median()) if len(brake_eps) else None,
            "max_s": float(brake_eps["duration_s"].max()) if len(brake_eps) else None,
        },
        "cruise_control_active_pct": round(100 * float((telemetry["cruise_control_active"] == 1).mean()), 2),
        "clutch_switch_available_pct": round(100 * float(telemetry["clutch_switch"].notna().mean()), 2),
    }
