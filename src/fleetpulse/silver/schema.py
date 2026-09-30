"""Silver schemas. Telemetry columns are generated from the verified signal definitions."""

from __future__ import annotations

from pyspark.sql.types import (
    ByteType,
    DateType,
    DoubleType,
    IntegerType,
    LongType,
    ShortType,
    StringType,
    StructField,
    StructType,
    TimestampNTZType,
)

from fleetpulse.j1939.definitions import MESSAGES
from fleetpulse.j1939.signals import SignalKind

# The Turku dataset is a single vehicle and no FMS vehicle identity is decoded
# in this layer (VI / PGN 65260 arrives via BAM and is not decoded). This is an
# explicit dataset-level identifier, not a value read from the CAN frames.
DATASET_VEHICLE_ID = "turku-renault-t520-6x2"

FRESH = "fresh"
STALE = "stale"
NO_OBSERVATION = "no_observation"

SIGNAL_EVENTS = StructType(
    [
        StructField("event_timestamp", TimestampNTZType(), False),
        StructField("event_date", DateType(), False),
        StructField("vehicle_id", StringType(), False),
        StructField("recording_session_id", StringType(), False),
        StructField("can_id", IntegerType(), False),
        StructField("priority", ByteType(), False),
        StructField("pgn", IntegerType(), False),
        StructField("source_address", ShortType(), False),
        StructField("message", StringType(), False),
        StructField("spn", IntegerType(), False),
        StructField("signal_name", StringType(), False),
        StructField("signal_raw", LongType(), True),
        StructField("signal_value", DoubleType(), True),
        StructField("signal_unit", StringType(), False),
        StructField("signal_status", StringType(), False),
        StructField("source_archive", StringType(), False),
        StructField("source_file", StringType(), False),
        StructField("source_line", LongType(), False),
        StructField("ingestion_run_id", StringType(), False),
        StructField("silver_build_id", StringType(), False),
    ]
)

RECORDING_SESSIONS = StructType(
    [
        StructField("vehicle_id", StringType(), False),
        StructField("recording_session_id", StringType(), False),
        StructField("session_index", IntegerType(), False),
        StructField("session_start", TimestampNTZType(), False),
        StructField("session_end", TimestampNTZType(), False),
        StructField("duration_s", DoubleType(), False),
        StructField("frame_count", LongType(), False),
        StructField("first_source_file", StringType(), False),
        StructField("last_source_file", StringType(), False),
        StructField("gap_before_s", DoubleType(), True),
        StructField("gap_before_kind", StringType(), True),
        StructField("gap_after_s", DoubleType(), True),
        StructField("recording_gap_threshold_s", DoubleType(), False),
        StructField("silver_build_id", StringType(), False),
    ]
)


def telemetry_schema() -> StructType:
    fields = [
        StructField("grid_timestamp", TimestampNTZType(), False),
        StructField("event_date", DateType(), False),
        StructField("vehicle_id", StringType(), False),
        StructField("recording_session_id", StringType(), False),
    ]
    for m in MESSAGES:
        for s in m.signals:
            dtype = ByteType() if s.kind is SignalKind.DISCRETE else DoubleType()
            fields.append(StructField(s.name, dtype, True))
            fields.append(StructField(f"{s.name}_status", StringType(), True))
        fields.append(StructField(f"{m.key}_age_ms", DoubleType(), True))
        fields.append(StructField(f"{m.key}_freshness", StringType(), False))
    fields.append(StructField("silver_build_id", StringType(), False))
    return StructType(fields)


TELEMETRY = telemetry_schema()
