"""silver_signal_events -> silver_telemetry: event-time as-of alignment on a 10 Hz grid.

Production contract (V1):

* **The recording session determines the grid lifetime.** Every recording
  session gets grid points every ``period_ms`` from ceil(session_start) to
  floor(session_end). A session shorter than one grid interval that contains
  no grid point yields no rows.
* **Each message determines only its own availability.** At grid time t a
  message contributes the latest frame with timestamp <= t (exact matches
  included) if t - frame_time <= max_age (3 x FMS repetition period, the
  reference ``max_age_ms``); otherwise its values are NULL and
  ``<msg>_freshness`` says ``stale`` (it was seen earlier in the session) or
  ``no_observation`` (not seen yet in the session). A message that stops or
  never appears does not shorten telemetry for the others.
* No look-ahead, no interpolation, no carry-over between recording sessions;
  a frame whose status is not valid yields NULL (``<signal>_status`` explains
  it), with no fallback to an older valid frame.

Relation to the reference (telemetry.alignment.build_telemetry): the value /
status / age at any grid time both produce is identical (validated cell by
cell). The reference grid instead spans only ceil(latest first observation) to
floor(earliest last observation) over the messages present, so it truncates a
session when one message type stops; Silver intentionally does not.

Implementation without a row-by-row as-of join: a frame serves exactly the grid
points ``[ceil(ts), min(next_ts - 1us, ts + max_age, grid_end)]`` (next_ts =
next frame of the same message in the session, via ``lead``), generated with
``sequence`` and joined to the grid.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from fleetpulse.j1939.definitions import MESSAGES
from fleetpulse.j1939.signals import SignalKind
from fleetpulse.silver.schema import FRESH, NO_OBSERVATION, STALE, TELEMETRY
from fleetpulse.silver.sessions import ts_micros
from fleetpulse.telemetry.alignment import DEFAULT_PERIOD_MS, max_age_ms

KEYS = ["recording_session_id"]


def _floor(us, period_us: int):
    return F.floor(us / F.lit(period_us)).cast("long") * F.lit(period_us)


def _ceil(us, period_us: int):
    return F.ceil(us / F.lit(period_us)).cast("long") * F.lit(period_us)


def message_frames(events: DataFrame) -> DataFrame:
    """Long signal events -> one row per decoded frame with <signal>_v / <signal>_s columns."""
    names = [s.name for m in MESSAGES for s in m.signals]
    return (
        events.groupBy(*KEYS, "message", "event_timestamp", "source_file", "source_line")
        .pivot("signal_name", names)
        .agg(F.first("signal_value").alias("v"), F.first("signal_status").alias("s"))
        .withColumn("ts_us", ts_micros("event_timestamp"))
    )


def grid_bounds(recording_sessions: DataFrame, period_us: int) -> DataFrame:
    """Per recording session: ceil(session_start) .. floor(session_end) in epoch us."""
    return recording_sessions.select(
        *KEYS,
        _ceil(ts_micros("session_start"), period_us).alias("grid_start_us"),
        _floor(ts_micros("session_end"), period_us).alias("grid_end_us"),
    )


def grid(bounds: DataFrame, period_us: int) -> DataFrame:
    return bounds.where(F.col("grid_start_us") <= F.col("grid_end_us")).select(
        *KEYS, F.explode(F.sequence("grid_start_us", "grid_end_us", F.lit(period_us))).alias("grid_us")
    )


def serve_message(frames: DataFrame, bounds: DataFrame, message, period_us: int) -> DataFrame:
    """Grid points served by each frame of ``message``: (session, grid_us, age, values, statuses)."""
    max_age_us = int(round(max_age_ms(message) * 1000))
    order = Window.partitionBy(*KEYS).orderBy("ts_us", "source_file", "source_line")
    f = frames.where(F.col("message") == message.acronym).join(bounds, KEYS)
    f = f.withColumn("next_us", F.lead("ts_us").over(order))
    first = _ceil(F.col("ts_us"), period_us)
    first = F.greatest(first, F.col("grid_start_us"))
    last = F.least(
        _floor(F.col("ts_us") + F.lit(max_age_us), period_us),
        F.coalesce(_floor(F.col("next_us") - F.lit(1), period_us), F.col("grid_end_us")),
        F.col("grid_end_us"),
    )
    cols = []
    for s in message.signals:
        v = F.col(f"{s.name}_v")
        cols.append((v.cast("tinyint") if s.kind is SignalKind.DISCRETE else v).alias(s.name))
        cols.append(F.col(f"{s.name}_s").alias(f"{s.name}_status"))
    served = f.where(first <= last).select(
        *KEYS, "ts_us", *cols, F.explode(F.sequence(first, last, F.lit(period_us))).alias("grid_us")
    )
    return served.select(
        *KEYS, "grid_us", ((F.col("grid_us") - F.col("ts_us")) / F.lit(1000.0)).alias(f"{message.key}_age_ms"),
        *[c for s in message.signals for c in (s.name, f"{s.name}_status")],
    )


def build(
    events: DataFrame,
    recording_sessions: DataFrame,
    vehicle_id: str,
    silver_build_id: str,
    period_ms: int = DEFAULT_PERIOD_MS,
) -> DataFrame:
    """``recording_sessions`` needs recording_session_id, session_start, session_end."""
    period_us = period_ms * 1000
    frames = message_frames(events).persist()
    bounds = grid_bounds(recording_sessions, period_us).persist()
    first_obs = frames.groupBy(*KEYS).pivot("message", [m.acronym for m in MESSAGES]).agg(F.min("ts_us"))

    tele = grid(bounds, period_us).join(first_obs, KEYS, "left")
    for m in MESSAGES:
        tele = tele.withColumnRenamed(m.acronym, f"_{m.key}_first_us")
    for m in MESSAGES:
        tele = tele.join(serve_message(frames, bounds, m, period_us), [*KEYS, "grid_us"], "left")
        age, first_us = F.col(f"{m.key}_age_ms"), F.col(f"_{m.key}_first_us")
        tele = tele.withColumn(
            f"{m.key}_freshness",
            F.when(age.isNotNull(), F.lit(FRESH))
            .when(first_us.isNull() | (F.col("grid_us") < first_us), F.lit(NO_OBSERVATION))
            .otherwise(F.lit(STALE)),
        )
    grid_ts = F.timestamp_micros("grid_us").cast("timestamp_ntz")
    tele = tele.select(
        "*",
        grid_ts.alias("grid_timestamp"),
        F.to_date(grid_ts).alias("event_date"),
        F.lit(vehicle_id).alias("vehicle_id"),
        F.lit(silver_build_id).alias("silver_build_id"),
    )
    return tele.select(*[F.col(f.name).cast(f.dataType).alias(f.name) for f in TELEMETRY.fields])
