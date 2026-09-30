import numpy as np
import pandas as pd

from fleetpulse.analysis.operating_states import StateThresholds, acceleration_mps2, episodes, label_states

T0 = pd.Timestamp("2021-01-19 10:00:00")


def _telemetry(speed_kph, rpm=600.0, pedal=0.0, load=10.0, brake=0):
    n = len(speed_kph)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(T0, periods=n, freq="100ms"),
            "engine_speed_rpm": rpm,
            "accelerator_pedal_pct": pedal,
            "engine_load_pct": load,
            "wheel_speed_kph": np.asarray(speed_kph, dtype=float),
            "cruise_control_active": pd.array([0] * n, dtype="Int8"),
            "brake_switch": pd.array([brake] * n, dtype="Int8"),
            "clutch_switch": pd.array([pd.NA] * n, dtype="Int8"),
        }
    )


def test_constant_acceleration_is_recovered():
    # 0.5 m/s^2 = 1.8 km/h per second = 0.18 km/h per 100 ms
    tel = _telemetry(20 + 0.18 * np.arange(200))
    a = acceleration_mps2(tel, StateThresholds()).dropna()
    assert np.allclose(a, 0.5)


def test_motion_states():
    th = StateThresholds()
    assert set(label_states(_telemetry([0.0] * 50), th)["motion_state"]) == {"stationary"}
    assert set(label_states(_telemetry([2.2] * 50), th)["motion_state"]) == {"creeping"}
    steady = label_states(_telemetry([40.0] * 100), th)["motion_state"]
    assert set(steady.iloc[20:80]) == {"cruising"}
    accel = label_states(_telemetry(20 + 0.18 * np.arange(200)), th)["motion_state"]
    assert set(accel.iloc[20:180]) == {"accelerating"}
    decel = label_states(_telemetry(60 - 0.18 * np.arange(200)), th)["motion_state"]
    assert set(decel.iloc[20:180]) == {"decelerating"}


def test_missing_speed_is_unknown_not_bridged():
    speed = np.r_[[40.0] * 50, [np.nan] * 5, [40.0] * 50]
    states = label_states(_telemetry(speed), StateThresholds())
    assert (states["motion_state"].iloc[50:55] == "unknown").all()
    assert states["acceleration_mps2"].iloc[50:55].isna().all()


def test_engine_and_demand_states():
    tel = _telemetry([0.0] * 4, rpm=[0.0, 150.0, 600.0, np.nan], pedal=[0, 0, 20, 0], load=[0, 5, 30, np.nan])
    s = label_states(tel, StateThresholds())
    assert s["engine_state"].tolist() == ["off", "transitional", "running", "unknown"]
    assert s["demand_state"].tolist() == ["zero_pedal_load", "zero_pedal", "pedal_pressed", "unknown"]


def test_braking_comes_from_brake_switch_only():
    s = label_states(_telemetry([30.0] * 10, brake=1), StateThresholds())
    assert s["braking"].all()


def test_episodes():
    tel = _telemetry(np.r_[[0.0] * 30, [2.0] * 20])
    eps = episodes(label_states(tel, StateThresholds()), "motion_state", 0.1)
    assert eps["state"].tolist() == ["stationary", "creeping"]
    assert eps["duration_s"].round(6).tolist() == [3.0, 2.0]
