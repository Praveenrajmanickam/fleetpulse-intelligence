"""Explicit Spark schemas for the Bronze tables.

Type choices
------------
* ``event_timestamp`` is TIMESTAMP_NTZ: the logger wrote wall-clock time with
  no timezone, so no timezone may be applied. The value round-trips to the
  exact source text (microseconds).
* ``can_id`` is INT: a CAN identifier is at most 29 bits (0x1FFFFFFF).
* ``dlc`` is TINYINT (0-8).
* ``byte_1`` .. ``byte_8`` are SMALLINT: Spark TINYINT is signed (-128..127),
  so it cannot hold 0-255. Bytes beyond the DLC are NULL, never 0/0xFF.
* ``ingested_at`` is TIMESTAMP (an instant, stored UTC).
* ``event_date`` is DATE derived from ``event_timestamp`` without any
  timezone conversion; it is the partition column.
"""

from __future__ import annotations

from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    ByteType,
    DateType,
    IntegerType,
    LongType,
    ShortType,
    StringType,
    StructField,
    StructType,
    TimestampNTZType,
    TimestampType,
)

MAX_DLC = 8
MAX_CAN_ID = 0x1FFFFFFF
PAYLOAD_COLUMNS = [f"byte_{i}" for i in range(1, MAX_DLC + 1)]

BRONZE_CAN_FRAMES = StructType(
    [
        StructField("event_timestamp", TimestampNTZType(), False),
        StructField("can_id", IntegerType(), False),
        StructField("dlc", ByteType(), False),
        *[StructField(c, ShortType(), True) for c in PAYLOAD_COLUMNS],
        StructField("source_archive", StringType(), False),
        StructField("source_file", StringType(), False),
        StructField("source_line", LongType(), False),
        StructField("ingested_at", TimestampType(), False),
        StructField("ingestion_run_id", StringType(), False),
        StructField("event_date", DateType(), False),
    ]
)

BRONZE_QUARANTINE = StructType(
    [
        StructField("raw_record", StringType(), True),
        StructField("source_archive", StringType(), False),
        StructField("source_file", StringType(), False),
        StructField("source_line", LongType(), False),
        StructField("failure_reason", StringType(), False),
        StructField("failure_reasons", ArrayType(StringType(), False), False),
        StructField("ingested_at", TimestampType(), False),
        StructField("ingestion_run_id", StringType(), False),
    ]
)

INGESTION_LOG = StructType(
    [
        StructField("source_archive", StringType(), False),
        StructField("source_file", StringType(), False),
        StructField("source_sha256", StringType(), False),
        StructField("source_size_bytes", LongType(), False),
        StructField("header_ok", BooleanType(), True),
        StructField("record_count", LongType(), True),
        StructField("valid_record_count", LongType(), True),
        StructField("invalid_record_count", LongType(), True),
        StructField("first_event_timestamp", TimestampNTZType(), True),
        StructField("last_event_timestamp", TimestampNTZType(), True),
        StructField("ingestion_run_id", StringType(), False),
        StructField("commit_id", StringType(), False),
        StructField("ingested_at", TimestampType(), False),
        StructField("status", StringType(), False),
        StructField("message", StringType(), True),
    ]
)

# Ingestion-log status values.
STATUS_INGESTED = "ingested"
STATUS_SKIPPED = "skipped_already_ingested"
STATUS_CONFLICT = "conflict_checksum_changed"

# Quarantine failure reasons, in priority order (failure_reason = first match).
FAILURE_REASONS = (
    "file_header_mismatch",
    "empty_line",
    "too_few_fields",
    "bad_timestamp",
    "bad_can_id",
    "can_id_exceeds_29_bits",
    "bad_dlc",
    "too_many_payload_fields",
    "bad_payload_byte",
    "payload_byte_out_of_range",
    "dlc_payload_mismatch",
)
