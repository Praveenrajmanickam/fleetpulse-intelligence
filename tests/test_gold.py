"""Gold V1 Idle Intelligence: event rules, summaries, scenario model, evidence objects."""

from datetime import datetime, timedelta

import pytest

pytest.importorskip("pyspark")

from fleetpulse.gold import evidence, idle  # noqa: E402
from fleetpulse.gold.config import IdleRuleConfig  # noqa: E402
from fleetpulse.gold.scenario import LABEL, Extrapolation, idle_scenario  # noqa: E402
from fleetpulse.j1939.definitions import MESSAGES  # noqa: E402
from fleetpulse.silver.schema import RECORDING_SESSIONS, TELEMETRY  # noqa: E402

T0 = datetime(2021, 2, 2, 8, 0, 0)
CFG = IdleRuleConfig()
VALUES = {"engine_speed_rpm": 600.0, "accelerator_pedal_pct": 0.0, "engine_load_pct": 12.0,
          "wheel_speed_kph": 0.0, "cruise_control_active": 0, "brake_switch": 0, "clutch_switch": None,
          "fuel_rate_lph": 2.0, "total_fuel_used_l": 100.0, "total_distance_m": 1000.0,
          "coolant_temp_c": 80.0, "ambient_temp_c": 1.0}


@pytest.fixture(scope="module")
def spark():
    from fleetpulse.bronze.spark import get_spark

    s = get_spark(app="gold-tests", master="local[2]", driver_memory="1g", shuffle_partitions=2)
    yield s
    s.stop()


def row(i, session="s1", start=T0, **over):
    """One 100 ms telemetry row; everything fresh+valid unless overridden.
    Overrides: signal values by name, '<msg>_freshness', '<signal>_status'."""
    ts = start + timedelta(milliseconds=100 * i)
    r = {"grid_timestamp": ts, "event_date": ts.date(), "vehicle_id": "v", "recording_session_id": session,
         "silver_build_id": "sb"}
    for m in MESSAGES:
        for s in m.signals:
            r[s.name] = VALUES[s.name]
            r[f"{s.name}_status"] = "not_available" if s.name == "clutch_switch" else "valid"
        r[f"{m.key}_age_ms"] = 10.0
        r[f"{m.key}_freshness"] = "fresh"
    r.update(over)
    if r["clutch_switch"] is None:
        r["clutch_switch_status"] = "not_available"
    return r


def seg(start_i, n, **over):
    return [row(i, **over) for i in range(start_i, start_i + n)]


def sessions_df(spark, ids_and_bounds):
    rows = [{"vehicle_id": "v", "recording_session_id": sid, "session_index": k, "session_start": a, "session_end": b,
             "duration_s": (b - a).total_seconds(), "frame_count": 1, "first_source_file": "part_3/a.csv",
             "last_source_file": "part_3/b.csv", "gap_before_s": None, "gap_before_kind": None, "gap_after_s": None,
             "recording_gap_threshold_s": 1.0, "silver_build_id": "sb"} for k, (sid, a, b) in enumerate(ids_and_bounds)]
    return spark.createDataFrame(rows, RECORDING_SESSIONS)


def build(spark, rows, cfg=CFG):
    tele = spark.createDataFrame(rows, TELEMETRY)
    by = {}
    for r in rows:
        a, b = by.get(r["recording_session_id"], (r["grid_timestamp"], r["grid_timestamp"]))
        by[r["recording_session_id"]] = (min(a, r["grid_timestamp"]), max(b, r["grid_timestamp"]))
    sess = sessions_df(spark, [(k, a, b) for k, (a, b) in by.items()])
    c = idle.classify(tele, cfg)
    runs = idle.run_table(idle.runs(c, cfg), cfg)
    events = idle.idle_events(runs, sess, cfg, "gb")
    summary = idle.session_summary(c, events, sess, cfg, "gb")
    op = idle.operating_summary(summary, cfg, "gb", "test")
    ev = sorted([r.asDict() for r in events.collect()], key=lambda e: e["event_start"])
    sm = {r.recording_session_id: r.asDict() for r in summary.collect()}
    return ev, sm, op.collect()[0].asDict()


MOVING = {"wheel_speed_kph": 30.0, "engine_speed_rpm": 1100.0, "fuel_rate_lph": 20.0}


def test_basic_event_between_driving(spark):
    rows = seg(0, 50, **MOVING) + seg(50, 200) + seg(250, 50, **MOVING)
    ev, sm, _ = build(spark, rows)
    (e,) = ev
    assert e["duration_seconds"] == pytest.approx(20.0)
    assert e["event_start"] == T0 + timedelta(seconds=5) and e["event_end"] == T0 + timedelta(seconds=24.9)
    assert (e["preceded_by"], e["followed_by"], e["preceded_by_driving"], e["followed_by_driving"]) == (
        "driving", "driving", True, True)
    assert e["fuel_consumed_l"] == pytest.approx(2.0 * 20 / 3600)
    assert (e["median_fuel_rate_lph"], e["maximum_fuel_rate_lph"], e["average_engine_rpm"]) == (2.0, 2.0, 600.0)
    assert e["detection_confidence"] == "high" and e["interpretation_status"] == "context_unknown"
    s = sm["s1"]
    assert s["moving_duration_s"] == pytest.approx(10.0) and s["stationary_engine_on_duration_s"] == pytest.approx(20.0)
    assert s["fuel_while_moving_l"] == pytest.approx(20.0 * 10 / 3600)
    assert s["stationary_pct_of_engine_on_time"] == pytest.approx(200 / 3)
    assert s["distance_km"] == pytest.approx(30.0 * 10 / 3600)


def test_engine_off_transition_splits_events(spark):
    rows = seg(0, 100) + seg(100, 50, engine_speed_rpm=0.0) + seg(150, 100)
    ev, sm, _ = build(spark, rows)
    assert [e["duration_seconds"] for e in ev] == pytest.approx([10.0, 10.0])
    assert (ev[0]["preceded_by"], ev[0]["followed_by"], ev[1]["preceded_by"]) == ("session_start", "engine_off", "engine_off")
    assert sm["s1"]["engine_off_duration_s"] == pytest.approx(5.0)
    assert "at_session_start" in ev[0]["descriptive_tags"]


def test_stale_speed_is_never_bridged(spark):
    rows = seg(0, 100) + [row(100, ccvs_freshness="stale")] + seg(101, 100)
    ev, sm, _ = build(spark, rows)
    assert len(ev) == 2 and ev[0]["followed_by"] == "signal_unavailable"
    assert sm["s1"]["engine_on_speed_unknown_s"] == pytest.approx(0.1)
    assert sm["s1"]["stationary_engine_on_duration_s"] == pytest.approx(20.0)  # unknown row not counted as idle


def test_missing_or_invalid_speed_value(spark):
    rows = seg(0, 60) + seg(60, 5, wheel_speed_kph=None, wheel_speed_kph_status="not_available") + seg(65, 60)
    ev, sm, _ = build(spark, rows)
    assert len(ev) == 2 and sm["s1"]["engine_on_speed_unknown_s"] == pytest.approx(0.5)


def test_unknown_engine_state_breaks_event(spark):
    rows = seg(0, 60) + seg(60, 3, eec1_freshness="stale") + seg(63, 60)
    ev, sm, _ = build(spark, rows)
    assert len(ev) == 2 and sm["s1"]["engine_state_unknown_s"] == pytest.approx(0.3)


def test_missing_fuel_keeps_event_but_lowers_coverage(spark):
    rows = seg(0, 100) + seg(100, 20, lfe_freshness="stale") + seg(120, 80)
    ev, sm, _ = build(spark, rows)
    (e,) = ev
    assert e["duration_seconds"] == pytest.approx(20.0)
    assert e["fuel_coverage_pct"] == pytest.approx(90.0) and e["detection_confidence"] == "medium"
    assert e["fuel_consumed_l"] == pytest.approx(2.0 * 18 / 3600)  # no fuel invented for the missing rows


def test_events_never_cross_session_boundaries(spark):
    t1 = T0 + timedelta(seconds=30)
    rows = seg(0, 100, session="a") + [row(i, session="b", start=t1) for i in range(100)]
    ev, sm, op = build(spark, rows)
    assert [e["recording_session_id"] for e in ev] == ["a", "b"]
    assert ev[0]["followed_by"] == "session_end" and ev[1]["preceded_by"] == "session_start"
    assert op["recording_sessions"] == 2 and op["stationary_events"] == 2


def test_grid_hole_breaks_event(spark):
    rows = seg(0, 50) + seg(60, 50)  # 1 s of grid missing
    ev, _, _ = build(spark, rows)
    assert len(ev) == 2


def test_short_run_not_an_event_but_counted_in_session(spark):
    rows = seg(0, 50, **MOVING) + seg(50, 15) + seg(65, 50, **MOVING)  # 1.5 s stationary
    ev, sm, op = build(spark, rows)
    assert ev == []
    assert sm["s1"]["stationary_engine_on_duration_s"] == pytest.approx(1.5)
    assert sm["s1"]["stationary_events"] == 0 and sm["s1"]["stationary_event_time_s"] == 0.0


def test_long_event_classes_and_counts(spark):
    rows = seg(0, 9100, coolant_temp_c=40.0, engine_speed_rpm=900.0)  # 15 min 10 s, cold, raised rpm
    ev, sm, op = build(spark, rows)
    (e,) = ev
    assert e["duration_class"] == "over_15_min"
    assert set(e["descriptive_tags"]) == {"over_15_min", "cold_engine_at_start", "raised_engine_speed", "at_session_start"}
    assert sm["s1"]["stationary_events_over_15_min"] == 1 and sm["s1"]["longest_stationary_event_s"] == pytest.approx(910.0)
    assert op["sessions_with_over_15_min_stationary"] == 1 and op["stationary_events_over_15_min"] == 1


def test_counter_fields_and_operating_summary(spark):
    rows = [row(i, total_fuel_used_l=100.0 + 0.01 * (i // 10), total_distance_m=1000.0 + 5 * (i // 10)) for i in range(100)]
    ev, sm, op = build(spark, rows)
    assert sm["s1"]["fuel_counter_l"] == pytest.approx(0.09) and sm["s1"]["distance_counter_km"] == pytest.approx(0.045)
    assert ev[0]["fuel_start_l"] == 100.0 and ev[0]["fuel_end_l"] == pytest.approx(100.09)
    assert op["period_type"] == "dataset_total" and op["timestamp_timezone"].startswith("unknown")
    assert op["stationary_fuel_consumed_l"] == pytest.approx(op["fuel_consumed_l"])


# --- scenario model -----------------------------------------------------------
def test_scenario_is_labelled_and_deterministic():
    s = idle_scenario(19.56, 25, 1.80)
    assert s["label"] == LABEL and "not observed savings" in s["label"]
    assert s["scenario_avoidable_fuel_l"] == pytest.approx(4.89)
    assert s["scenario_cost_exposure"] == pytest.approx(8.802)
    assert s["projection"] is None
    assert idle_scenario(19.56, 0, 1.8)["scenario_cost_exposure"] == 0


@pytest.mark.parametrize("args", [(-1, 10, 1), (10, 101, 1), (10, -5, 1), (10, 10, -1)])
def test_scenario_rejects_invalid_inputs(args):
    with pytest.raises(ValueError):
        idle_scenario(*args)


def test_projection_only_with_explicit_assumption():
    with pytest.raises(ValueError):
        Extrapolation(24, 365, "  ")
    s = idle_scenario(10, 50, 2, extrapolation=Extrapolation(24, 360, "operator: period is representative"))
    assert s["projection"]["scale_factor"] == 15 and s["projection"]["projected_avoidable_fuel_l"] == 75
    assert "SCENARIO / ESTIMATE" in s["projection"]["label"]


# --- evidence objects ---------------------------------------------------------
def test_evidence_objects(spark):
    rows = seg(0, 50, **MOVING) + seg(50, 3100) + seg(3150, 50, **MOVING) + seg(3200, 100)
    ev, _, _ = build(spark, rows)
    cfg = IdleRuleConfig(evidence_top_n_by_fuel=1)
    chosen = evidence.select_important(ev, cfg)
    assert [e["duration_seconds"] for e in chosen] == pytest.approx([310.0])  # >= 5 min; also the top by fuel
    o = evidence.evidence_object(chosen[0], cfg)
    assert o["event_type"] == "engine_on_stationary" and "fuel_waste" in o["prohibited_claims"]
    assert "cost_savings" in o["prohibited_claims"] and o["confidence"]["interpretation"] == "context_unknown"
    assert o["measured"]["fuel_consumed_l"] == pytest.approx(round(2.0 * 310 / 3600, 3))
    assert o["lineage"]["event_id"] == chosen[0]["event_id"] and o["time_range"]["timezone"].startswith("unknown")
