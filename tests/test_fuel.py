import numpy as np
import pandas as pd
import pytest

from fleetpulse.analysis.fuel import analyse, coasting, counter_delta
from fleetpulse.analysis.operating_states import StateThresholds, label_states

T0 = pd.Timestamp("2021-01-19 10:00:00")


def _telemetry(speed, rpm, pedal, load, fuel_rate, brake=0):
    n = len(speed)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(T0, periods=n, freq="100ms"),
            "engine_speed_rpm": np.broadcast_to(rpm, n).astype(float),
            "accelerator_pedal_pct": np.broadcast_to(pedal, n).astype(float),
            "engine_load_pct": np.broadcast_to(load, n).astype(float),
            "wheel_speed_kph": np.asarray(speed, dtype=float),
            "cruise_control_active": pd.array([0] * n, dtype="Int8"),
            "brake_switch": pd.array(np.broadcast_to(brake, n), dtype="Int8"),
            "clutch_switch": pd.array([pd.NA] * n, dtype="Int8"),
            "fuel_rate_lph": np.broadcast_to(fuel_rate, n).astype(float),
            "coolant_temp_c": 80.0,
            "ambient_temp_c": 1.0,
        }
    )


def _counters(fuel_l, dist_m, seconds):
    t = [T0, T0 + pd.Timedelta(seconds=seconds)]
    return {
        "hrlfc": pd.DataFrame({"timestamp": t, "total_fuel_used_l": fuel_l}),
        "vdhr": pd.DataFrame({"timestamp": t, "total_distance_m": dist_m}),
    }


def test_counter_delta():
    d = counter_delta(pd.DataFrame({"timestamp": [T0, T0 + pd.Timedelta(seconds=1)], "x": [10.0, 10.5]}), "x")
    assert d["delta"] == 0.5 and d["monotonic"]


def test_totals_and_integration():
    # 36 L/h for 100 s = 1.0 L; 36 km/h for 100 s = 1.0 km
    tel = _telemetry([36.0] * 1000, rpm=1000, pedal=20, load=30, fuel_rate=36.0)
    states = label_states(tel, StateThresholds())
    summary, details = analyse(tel, states, _counters([100.0, 101.0], [0.0, 1000.0], 100))
    t = summary["totals"]
    assert t["fuel_l_integrated_rate"] == pytest.approx(1.0)
    assert t["distance_km_integrated_speed"] == pytest.approx(1.0)
    assert t["km_per_l"] == pytest.approx(1.0)
    assert t["counter_to_integrated_fuel_ratio"] == pytest.approx(1.0)
    assert details["operating_zones"]["fuel_share_pct"].sum() == pytest.approx(100.0)


def test_stop_fuel_is_attributed():
    speed = np.r_[[20.0] * 100, [0.0] * 200, [20.0] * 100]
    fuel_rate = np.r_[[10.0] * 100, [1.8] * 200, [10.0] * 100]
    tel = _telemetry(speed, rpm=600, pedal=0, load=10, fuel_rate=fuel_rate)
    states = label_states(tel, StateThresholds())
    summary, details = analyse(tel, states, _counters([0.0, 0.1], [0.0, 100.0], 40))
    stops = details["stops"]
    assert len(stops) == 1
    assert stops.loc[0, "duration_s"] == pytest.approx(20.0)
    assert stops.loc[0, "fuel_l"] == pytest.approx(1.8 * 20 / 3600)


def test_coasting_split_by_engine_speed():
    speed = 40 - 0.05 * np.arange(200)  # falling ~0.14 m/s^2
    rpm = np.r_[[600.0] * 100, [1000.0] * 100]
    fuel_rate = np.r_[[1.3] * 100, [0.0] * 100]
    tel = _telemetry(speed, rpm=rpm, pedal=0, load=np.r_[[6.0] * 100, [0.0] * 100], fuel_rate=fuel_rate)
    states = label_states(tel, StateThresholds())
    from fleetpulse.analysis.fuel import add_fuel_columns

    samples, eps = coasting(add_fuel_columns(tel), states, 0.1)
    assert set(eps["state"]) == {"engine_at_idle", "engine_above_idle"}
    idle = samples[samples["coast_kind"] == "engine_at_idle"]["fuel_rate_lph"]
    above = samples[samples["coast_kind"] == "engine_above_idle"]["fuel_rate_lph"]
    assert (idle == 1.3).all() and (above == 0).all()


def test_brake_or_pedal_excludes_coasting():
    speed = 40 - 0.05 * np.arange(200)
    tel = _telemetry(speed, rpm=1000, pedal=0, load=0, fuel_rate=0.0, brake=1)
    states = label_states(tel, StateThresholds())
    from fleetpulse.analysis.fuel import add_fuel_columns

    samples, eps = coasting(add_fuel_columns(tel), states, 0.1)
    assert samples.empty and eps.empty
