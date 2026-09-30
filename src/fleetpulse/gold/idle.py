"""Idle Intelligence: silver_telemetry -> gold_idle_events / gold_session_summary / gold_operating_summary.

Row classification (only fresh + valid verified signals are trusted):

    rpm_ok   = eec1_freshness = 'fresh' AND engine_speed_rpm_status = 'valid'
    speed_ok = ccvs_freshness = 'fresh' AND wheel_speed_kph_status = 'valid'
    fuel_ok  = lfe_freshness  = 'fresh' AND fuel_rate_lph_status  = 'valid'

    engine_state_unknown     NOT rpm_ok
    engine_off               rpm_ok AND rpm <  engine_on_min_rpm
    engine_on_speed_unknown  rpm_ok AND rpm >= engine_on_min_rpm AND NOT speed_ok
    engine_on_stationary     rpm >= engine_on_min_rpm AND speed_ok AND speed <  stationary_max_kph
    engine_on_moving         rpm >= engine_on_min_rpm AND speed_ok AND speed >= stationary_max_kph

A *run* is a maximal sequence of consecutive grid rows (100 ms apart) of one
recording session with the same state. An *idle event* is an
``engine_on_stationary`` run of at least ``min_event_s``. So an event is broken
by: moving, engine off, ANY unknown row (stale/invalid/missing speed or engine
speed - never bridged), a hole in the grid, or the session boundary.

Fuel: ``fuel_rate_lph x 0.1 s / 3600`` summed over rows with fuel_ok (the
method validated against the HRLFC SPN 5054 counter within 0.1 % per session);
rows without valid fuel contribute nothing and lower ``fuel_coverage_pct``.
The counter readings at event start/end are reported too, but at 1 Hz with
~0.01 L steps they are too coarse for short events.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from fleetpulse.gold.config import IdleRuleConfig

STATES = ("engine_on_stationary", "engine_on_moving", "engine_on_speed_unknown", "engine_off", "engine_state_unknown")
NEIGHBOUR = {  # run state -> preceded_by / followed_by label
    "engine_on_moving": "driving",
    "engine_off": "engine_off",
    "engine_on_speed_unknown": "signal_unavailable",
    "engine_state_unknown": "signal_unavailable",
    "engine_on_stationary": "stationary_short",  # cannot happen next to itself; kept for completeness
}


def _us(c):
    return F.unix_micros(F.col(c).cast("timestamp"))


def classify(tele: DataFrame, cfg: IdleRuleConfig) -> DataFrame:
    rpm_ok = (F.col("eec1_freshness") == "fresh") & (F.col("engine_speed_rpm_status") == "valid")
    speed_ok = (F.col("ccvs_freshness") == "fresh") & (F.col("wheel_speed_kph_status") == "valid")
    fuel_ok = (F.col("lfe_freshness") == "fresh") & (F.col("fuel_rate_lph_status") == "valid")
    load_ok = (F.col("eec2_freshness") == "fresh") & (F.col("engine_load_pct_status") == "valid")
    cool_ok = (F.col("et1_freshness") == "fresh") & (F.col("coolant_temp_c_status") == "valid")
    ctr_ok = (F.col("hrlfc_freshness") == "fresh") & (F.col("total_fuel_used_l_status") == "valid")
    dist_ok = (F.col("vdhr_freshness") == "fresh") & (F.col("total_distance_m_status") == "valid")
    on = rpm_ok & (F.col("engine_speed_rpm") >= cfg.engine_on_min_rpm)
    state = (
        F.when(~rpm_ok, "engine_state_unknown")
        .when(~on, "engine_off")
        .when(~speed_ok, "engine_on_speed_unknown")
        .when(F.col("wheel_speed_kph") < cfg.stationary_max_kph, "engine_on_stationary")
        .otherwise("engine_on_moving")
    )
    dt = cfg.grid_period_s
    return tele.select(
        "vehicle_id", "recording_session_id", "grid_timestamp", "silver_build_id",
        state.alias("state"),
        F.when(fuel_ok, F.col("fuel_rate_lph")).alias("fuel_rate"),
        F.when(fuel_ok, F.col("fuel_rate_lph") * F.lit(dt / 3600)).alias("fuel_l"),
        F.when(speed_ok, F.col("wheel_speed_kph") * F.lit(dt / 3600)).alias("distance_km"),
        F.when(rpm_ok, F.col("engine_speed_rpm")).alias("rpm"),
        F.when(load_ok, F.col("engine_load_pct")).alias("load"),
        F.when(cool_ok, F.col("coolant_temp_c")).alias("coolant"),
        F.when(ctr_ok, F.col("total_fuel_used_l")).alias("fuel_counter"),
        F.when(dist_ok, F.col("total_distance_m")).alias("distance_counter"),
    )


def runs(rows: DataFrame, cfg: IdleRuleConfig) -> DataFrame:
    """Assign run ids: a new run starts on a state change or a grid hole."""
    w = Window.partitionBy("recording_session_id").orderBy("grid_timestamp")
    period_us = int(round(cfg.grid_period_s * 1e6))
    prev_state = F.lag("state").over(w)
    prev_ts = F.lag(_us("grid_timestamp")).over(w)
    new_run = prev_state.isNull() | (prev_state != F.col("state")) | ((_us("grid_timestamp") - prev_ts) != period_us)
    r = rows.withColumn("_new", new_run.cast("int"))
    return r.withColumn("run_id", F.sum("_new").over(w.rowsBetween(Window.unboundedPreceding, 0))).drop("_new")


def _first(valid_col, ts="grid_timestamp"):
    return F.min(F.when(F.col(valid_col).isNotNull(), F.struct(F.col(ts), F.col(valid_col))))[valid_col]


def _last(valid_col, ts="grid_timestamp"):
    return F.max(F.when(F.col(valid_col).isNotNull(), F.struct(F.col(ts), F.col(valid_col))))[valid_col]


def run_table(r: DataFrame, cfg: IdleRuleConfig) -> DataFrame:
    dt = cfg.grid_period_s
    agg = r.groupBy("vehicle_id", "recording_session_id", "silver_build_id", "run_id", "state").agg(
        F.min("grid_timestamp").alias("start"), F.max("grid_timestamp").alias("end"),
        F.count(F.lit(1)).alias("grid_rows"),
        F.sum("fuel_l").alias("fuel_l"), F.count("fuel_l").alias("fuel_rows"),
        F.avg("fuel_rate").alias("avg_fr"), F.median("fuel_rate").alias("med_fr"), F.max("fuel_rate").alias("max_fr"),
        F.avg("rpm").alias("avg_rpm"), F.avg("load").alias("avg_load"),
        _first("coolant").alias("coolant_start"), _last("coolant").alias("coolant_end"),
        _first("fuel_counter").alias("counter_start"), _last("fuel_counter").alias("counter_end"),
        F.sum("distance_km").alias("distance_km"),
    ).withColumn("duration_s", F.col("grid_rows") * F.lit(dt))
    w = Window.partitionBy("recording_session_id").orderBy("run_id")
    label = F.create_map(*[x for k, v in NEIGHBOUR.items() for x in (F.lit(k), F.lit(v))])
    return agg.select(
        "*",
        F.coalesce(label[F.lag("state").over(w)], F.lit("session_start")).alias("preceded_by"),
        F.coalesce(label[F.lead("state").over(w)], F.lit("session_end")).alias("followed_by"),
    )


def idle_events(runs_df: DataFrame, sessions: DataFrame, cfg: IdleRuleConfig, gold_build_id: str) -> DataFrame:
    e = runs_df.where((F.col("state") == "engine_on_stationary") & (F.col("duration_s") >= cfg.min_event_s))
    e = e.join(
        F.broadcast(sessions.select("recording_session_id", F.col("first_source_file").alias("session_first_source_file"),
                                    F.col("last_source_file").alias("session_last_source_file"))),
        "recording_session_id", "left",
    )
    coverage = F.col("fuel_rows") / F.col("grid_rows") * 100
    tags = F.array_compact(F.array(
        F.when(F.col("coolant_start") < cfg.cold_coolant_c, F.lit("cold_engine_at_start")),
        F.when(F.col("avg_rpm") > cfg.raised_idle_rpm, F.lit("raised_engine_speed")),
        F.when(F.col("duration_s") >= cfg.long_event_s, F.lit("over_15_min")),
        F.when(F.col("preceded_by") == "session_start", F.lit("at_session_start")),
    ))
    duration_class = (F.when(F.col("duration_s") < 60, "under_1_min").when(F.col("duration_s") < 300, "1_to_5_min")
                      .when(F.col("duration_s") < cfg.long_event_s, "5_to_15_min").otherwise("over_15_min"))
    confidence = F.when(coverage >= 99.999, "high").when(coverage >= 90, "medium").otherwise("low")
    return e.select(
        F.concat_ws("-", "recording_session_id", F.lit("idle"), F.date_format("start", "HHmmssSSSSSS")).alias("event_id"),
        "vehicle_id", "recording_session_id",
        F.col("start").alias("event_start"), F.col("end").alias("event_end"), F.col("duration_s").alias("duration_seconds"),
        duration_class.alias("duration_class"), "grid_rows",
        F.col("counter_start").alias("fuel_start_l"), F.col("counter_end").alias("fuel_end_l"),
        (F.col("counter_end") - F.col("counter_start")).alias("fuel_counter_delta_l"),
        F.col("fuel_l").alias("fuel_consumed_l"),
        F.lit("LFE SPN 183 fuel rate integrated on the 100 ms grid (fresh+valid rows)").alias("fuel_method"),
        coverage.alias("fuel_coverage_pct"),
        F.col("avg_fr").alias("average_fuel_rate_lph"), F.col("med_fr").alias("median_fuel_rate_lph"),
        F.col("max_fr").alias("maximum_fuel_rate_lph"),
        F.col("avg_rpm").alias("average_engine_rpm"), F.col("avg_load").alias("average_engine_load_pct"),
        F.col("coolant_start").alias("coolant_start_c"), F.col("coolant_end").alias("coolant_end_c"),
        "preceded_by", "followed_by",
        (F.col("preceded_by") == "driving").alias("preceded_by_driving"),
        (F.col("followed_by") == "driving").alias("followed_by_driving"),
        tags.alias("descriptive_tags"), confidence.alias("detection_confidence"),
        F.lit("context_unknown").alias("interpretation_status"), F.lit(cfg.rule_version).alias("rule_version"),
        "session_first_source_file", "session_last_source_file", "silver_build_id",
        F.lit(gold_build_id).alias("gold_build_id"),
    )


def session_summary(rows: DataFrame, events: DataFrame, sessions: DataFrame, cfg: IdleRuleConfig,
                    gold_build_id: str) -> DataFrame:
    dt = cfg.grid_period_s
    def t(state):  # noqa: E306
        return F.sum((F.col("state") == state).cast("long")) * F.lit(dt)

    def fuel_in(*states):
        return F.coalesce(F.sum(F.when(F.col("state").isin(*states), F.col("fuel_l"))), F.lit(0.0))

    s = rows.groupBy("vehicle_id", "recording_session_id", "silver_build_id").agg(
        F.count(F.lit(1)).alias("grid_rows"),
        (t("engine_on_stationary") + t("engine_on_moving") + t("engine_on_speed_unknown")).alias("engine_on_duration_s"),
        t("engine_on_moving").alias("moving_duration_s"),
        t("engine_on_stationary").alias("stationary_engine_on_duration_s"),
        t("engine_on_speed_unknown").alias("engine_on_speed_unknown_s"),
        t("engine_off").alias("engine_off_duration_s"), t("engine_state_unknown").alias("engine_state_unknown_s"),
        F.coalesce(F.sum("distance_km"), F.lit(0.0)).alias("distance_km"),
        ((_last("distance_counter") - _first("distance_counter")) / 1000).alias("distance_counter_km"),
        F.coalesce(F.sum("fuel_l"), F.lit(0.0)).alias("fuel_consumed_l"),
        (_last("fuel_counter") - _first("fuel_counter")).alias("fuel_counter_l"),
        fuel_in("engine_on_moving").alias("fuel_while_moving_l"),
        fuel_in("engine_on_stationary").alias("fuel_while_stationary_l"),
        fuel_in("engine_on_speed_unknown").alias("fuel_while_speed_unknown_l"),
        (F.count("fuel_l") / F.count(F.lit(1)) * 100).alias("fuel_coverage_pct"),
    )
    ev = events.groupBy("recording_session_id").agg(
        F.count(F.lit(1)).cast("int").alias("stationary_events"),
        F.sum("duration_seconds").alias("stationary_event_time_s"),
        F.max("duration_seconds").alias("longest_stationary_event_s"),
        F.sum((F.col("duration_seconds") >= cfg.long_event_s).cast("int")).alias("stationary_events_over_15_min"),
    )
    return (
        s.join(sessions.select("recording_session_id", "session_start", "session_end",
                               F.col("duration_s").alias("session_duration_s")), "recording_session_id", "left")
        .join(ev, "recording_session_id", "left")
        .select(
            "*",
            F.when(F.col("engine_on_duration_s") > 0,
                   F.col("stationary_engine_on_duration_s") / F.col("engine_on_duration_s") * 100)
            .alias("stationary_pct_of_engine_on_time"),
            F.lit(cfg.rule_version).alias("rule_version"), F.lit(gold_build_id).alias("gold_build_id"),
        )
        .fillna({"stationary_events": 0, "stationary_event_time_s": 0.0, "stationary_events_over_15_min": 0})
    )


def operating_summary(sess: DataFrame, cfg: IdleRuleConfig, gold_build_id: str, scope: str) -> DataFrame:
    """One row, period_type='dataset_total'. No per-day rows: the logger timezone is
    unknown, so a calendar day would not be a business day."""
    h = 3600.0
    return sess.agg(
        F.lit("dataset_total").alias("period_type"), F.lit("all processed recording sessions").alias("period_label"),
        F.min("session_start").alias("period_start"), F.max("session_end").alias("period_end"),
        F.lit("unknown (logger wall clock)").alias("timestamp_timezone"), F.lit(scope).alias("scope"),
        F.count(F.lit(1)).cast("int").alias("recording_sessions"),
        F.sum((F.col("engine_on_duration_s") > 0).cast("int")).alias("sessions_with_engine_on"),
        (F.sum("engine_on_duration_s") / h).alias("engine_on_hours"),
        (F.sum("moving_duration_s") / h).alias("moving_hours"),
        (F.sum("stationary_engine_on_duration_s") / h).alias("stationary_engine_on_hours"),
        (F.sum("stationary_engine_on_duration_s") / F.sum("engine_on_duration_s") * 100).alias("stationary_pct_of_engine_on_time"),
        F.sum("distance_km").alias("distance_km"), F.sum("fuel_consumed_l").alias("fuel_consumed_l"),
        F.sum("fuel_while_stationary_l").alias("stationary_fuel_consumed_l"),
        (F.sum("fuel_while_stationary_l") / F.sum("fuel_consumed_l") * 100).alias("stationary_fuel_pct"),
        F.sum("stationary_events").cast("int").alias("stationary_events"),
        F.sum("stationary_events_over_15_min").cast("int").alias("stationary_events_over_15_min"),
        F.sum((F.col("stationary_engine_on_duration_s") > cfg.long_event_s).cast("int")).alias("sessions_with_over_15_min_stationary"),
        F.lit(cfg.rule_version).alias("rule_version"), F.lit(gold_build_id).alias("gold_build_id"),
    )
