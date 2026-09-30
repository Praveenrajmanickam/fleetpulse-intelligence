"""Gold V1 schemas (Idle Intelligence)."""

from __future__ import annotations

from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampNTZType,
)


def _f(name, t, nullable=True):
    return StructField(name, t, nullable)


D, S, L, I, B, TS = DoubleType(), StringType(), LongType(), IntegerType(), BooleanType(), TimestampNTZType()

GOLD_IDLE_EVENTS = StructType([
    _f("event_id", S, False), _f("vehicle_id", S, False), _f("recording_session_id", S, False),
    _f("event_start", TS, False), _f("event_end", TS, False), _f("duration_seconds", D, False),
    _f("duration_class", S, False), _f("grid_rows", L, False),
    _f("fuel_start_l", D), _f("fuel_end_l", D), _f("fuel_counter_delta_l", D),
    _f("fuel_consumed_l", D), _f("fuel_method", S, False), _f("fuel_coverage_pct", D, False),
    _f("average_fuel_rate_lph", D), _f("median_fuel_rate_lph", D), _f("maximum_fuel_rate_lph", D),
    _f("average_engine_rpm", D), _f("average_engine_load_pct", D),
    _f("coolant_start_c", D), _f("coolant_end_c", D),
    _f("preceded_by", S, False), _f("followed_by", S, False),
    _f("preceded_by_driving", B, False), _f("followed_by_driving", B, False),
    _f("descriptive_tags", ArrayType(S, False), False),
    _f("detection_confidence", S, False), _f("interpretation_status", S, False),
    _f("rule_version", S, False), _f("session_first_source_file", S), _f("session_last_source_file", S),
    _f("silver_build_id", S, False), _f("gold_build_id", S, False),
])

GOLD_SESSION_SUMMARY = StructType([
    _f("vehicle_id", S, False), _f("recording_session_id", S, False),
    _f("session_start", TS, False), _f("session_end", TS, False), _f("session_duration_s", D, False),
    _f("grid_rows", L, False),
    _f("engine_on_duration_s", D, False), _f("moving_duration_s", D, False),
    _f("stationary_engine_on_duration_s", D, False), _f("engine_on_speed_unknown_s", D, False),
    _f("engine_off_duration_s", D, False), _f("engine_state_unknown_s", D, False),
    _f("distance_km", D, False), _f("distance_counter_km", D),
    _f("fuel_consumed_l", D, False), _f("fuel_counter_l", D),
    _f("fuel_while_moving_l", D, False), _f("fuel_while_stationary_l", D, False),
    _f("fuel_while_speed_unknown_l", D, False),
    _f("stationary_pct_of_engine_on_time", D),
    _f("stationary_events", I, False), _f("stationary_event_time_s", D, False),
    _f("longest_stationary_event_s", D), _f("stationary_events_over_15_min", I, False),
    _f("fuel_coverage_pct", D, False),
    _f("rule_version", S, False), _f("silver_build_id", S, False), _f("gold_build_id", S, False),
])

GOLD_OPERATING_SUMMARY = StructType([
    _f("period_type", S, False), _f("period_label", S, False),
    _f("period_start", TS), _f("period_end", TS), _f("timestamp_timezone", S, False),
    _f("scope", S, False),
    _f("recording_sessions", I, False), _f("sessions_with_engine_on", I, False),
    _f("engine_on_hours", D, False), _f("moving_hours", D, False), _f("stationary_engine_on_hours", D, False),
    _f("stationary_pct_of_engine_on_time", D),
    _f("distance_km", D, False), _f("fuel_consumed_l", D, False), _f("stationary_fuel_consumed_l", D, False),
    _f("stationary_fuel_pct", D),
    _f("stationary_events", I, False), _f("stationary_events_over_15_min", I, False),
    _f("sessions_with_over_15_min_stationary", I, False),
    _f("rule_version", S, False), _f("gold_build_id", S, False),
])

TABLES = {
    "gold_idle_events": GOLD_IDLE_EVENTS,
    "gold_session_summary": GOLD_SESSION_SUMMARY,
    "gold_operating_summary": GOLD_OPERATING_SUMMARY,
}
