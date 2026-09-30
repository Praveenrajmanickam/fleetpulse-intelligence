"""Spark parsing of raw Turku CAN CSV text into Bronze frames. Pure transformations.

Source format (same as the reference reader, fleetpulse.ingestion.can_csv)::

    timestamp;id;dlc;data                                   <- header, line 1
    2021-01-19 10:12:59.220499;0xcf004e6;8;240;125;131;194;18;255;255;255
    2021-01-19 10:16:51;0x10ff80e6;1;230                    <- whole second, DLC 1

The header names a single ``data`` column, but every payload byte is its own
field, so a record has 3 + DLC fields. Lines may end in CRLF.

Rules (matching the reference reader for valid data):
* ``source_line`` is the 1-based line number in the file (header = 1).
* timestamps: ``yyyy-MM-dd HH:mm:ss.S{1..6}`` or, when the logger omitted the
  fraction (microseconds == 0), ``yyyy-MM-dd HH:mm:ss``; parsed as
  TIMESTAMP_NTZ, never shifted by a timezone.
* CAN ID: hex, optional ``0x`` prefix, <= 0x1FFFFFFF.
* DLC 0-8 and exactly DLC payload fields, each an integer 0-255.
* A trailing newline at end of file does not create a record; any other
  empty line is a malformed record and is quarantined.

Deliberate differences from the reference reader (malformed input only):
records with > 8 payload fields and blank lines are quarantined per record
instead of failing the whole file / being skipped silently.

Every record is either valid or quarantined, never dropped. Array access uses
``try_element_at`` because Spark 4 runs in ANSI mode, where ``element_at`` on a
short record raises instead of returning NULL (one malformed line would abort
the batch).
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from fleetpulse.bronze.schema import MAX_CAN_ID, MAX_DLC, PAYLOAD_COLUMNS
from fleetpulse.ingestion.can_csv import EXPECTED_HEADER

TS_FRACTION = "yyyy-MM-dd HH:mm:ss.SSSSSS"  # accepts 1-6 fraction digits
TS_WHOLE_SECOND = "yyyy-MM-dd HH:mm:ss"


def read_staged_files(spark, staged_dir: str) -> DataFrame:
    """One row per staged file: ``staged_name``, ``value`` (whole file text)."""
    # wholetext must be passed to .text(); an .option() is overridden by its default.
    df = spark.read.text(staged_dir, wholetext=True)
    return df.select(
        F.try_element_at(F.split(F.col("_metadata.file_path"), "/"), F.lit(-1)).alias("staged_name"),
        F.col("value"),
    )


def explode_lines(files: DataFrame) -> DataFrame:
    """File text -> one row per data line with ``source_line`` and ``header_ok``.

    Expects ``value`` (file text) plus any lineage columns, which are kept.
    """
    keep = [c for c in files.columns if c != "value"]
    # Per-file values are computed BEFORE the explode, in their own projections.
    # If they sit next to posexplode, Spark evaluates them after the generator,
    # i.e. re-splits the whole file text once per output line (O(lines x bytes)).
    split = files.select(*keep, F.split(F.col("value"), "\n", -1).alias("_lines"))
    per_file = split.select(
        *keep,
        "_lines",
        (F.regexp_replace(F.try_element_at("_lines", F.lit(1)), "\r$", "") == F.lit(EXPECTED_HEADER)).alias(
            "header_ok"
        ),
        F.size("_lines").alias("_n_lines"),
    )
    # _lines is consumed by the generator only, so it is not copied into every output row.
    exploded = per_file.select(*keep, "header_ok", "_n_lines", F.posexplode("_lines").alias("_pos", "_line"))
    # Drop the header (pos 0) and the empty string after a final newline.
    is_final_newline = (F.col("_pos") == F.col("_n_lines") - 1) & (F.col("_line") == "")
    return exploded.where((F.col("_pos") > 0) & ~is_final_newline).select(
        *keep,
        "header_ok",
        (F.col("_pos") + 1).cast("long").alias("source_line"),
        F.regexp_replace("_line", "\r$", "").alias("raw_record"),
    )


def _payload_ok(payload: Column) -> Column:
    return F.forall(payload, lambda x: x.rlike("^[0-9]{1,3}$"))


def parse_records(lines: DataFrame) -> DataFrame:
    """Parse ``raw_record`` and attach ``failure_reasons`` (empty when valid)."""
    fields = F.split("raw_record", ";", -1)
    n_fields = F.size(fields)
    ts_raw = F.try_element_at(fields, F.lit(1))
    id_raw = F.try_element_at(fields, F.lit(2))
    dlc_raw = F.try_element_at(fields, F.lit(3))
    payload = F.slice(fields, 4, F.greatest(n_fields - 3, F.lit(0)))
    n_payload = F.size(payload)

    ts = F.coalesce(
        F.try_to_timestamp(ts_raw, F.lit(TS_FRACTION)), F.try_to_timestamp(ts_raw, F.lit(TS_WHOLE_SECOND))
    ).cast("timestamp_ntz")
    id_ok = F.coalesce(id_raw.rlike("^(0[xX])?[0-9a-fA-F]{1,8}$"), F.lit(False))
    can_id = F.when(id_ok, F.conv(F.regexp_replace(id_raw, "^0[xX]", ""), 16, 10).cast("long"))
    dlc_digits = F.coalesce(dlc_raw.rlike("^[0-9]{1,2}$"), F.lit(False))
    dlc = F.when(dlc_digits, dlc_raw.cast("int"))
    dlc_ok = dlc_digits & dlc.between(0, MAX_DLC)
    payload_digits = _payload_ok(payload)
    payload_ints = F.transform(payload, lambda x: F.when(x.rlike("^[0-9]{1,3}$"), x.cast("int")))

    checks = [
        ("file_header_mismatch", ~F.col("header_ok")),
        ("empty_line", F.col("raw_record") == ""),
        ("too_few_fields", n_fields < 3),
        ("bad_timestamp", ts.isNull()),
        ("bad_can_id", ~id_ok),
        ("can_id_exceeds_29_bits", id_ok & (can_id > MAX_CAN_ID)),
        ("bad_dlc", ~dlc_ok),
        ("too_many_payload_fields", n_payload > MAX_DLC),
        ("bad_payload_byte", ~payload_digits),
        ("payload_byte_out_of_range", payload_digits & F.exists(payload_ints, lambda x: x > 255)),
        ("dlc_payload_mismatch", dlc_ok & (n_payload != dlc)),
    ]
    reasons = F.filter(
        F.array(*[F.when(cond, F.lit(name)) for name, cond in checks]), lambda x: x.isNotNull()
    )
    return lines.select(
        "*",
        ts.alias("event_timestamp"),
        can_id.alias("_can_id_long"),
        dlc.alias("_dlc_int"),
        payload_ints.alias("_payload"),
        reasons.alias("failure_reasons"),
    )


def to_bronze(parsed: DataFrame) -> DataFrame:
    """Valid records in the Bronze frame layout (lineage/run columns kept as-is)."""
    valid = parsed.where(F.size("failure_reasons") == 0)
    byte_cols = [
        F.when(F.lit(i) <= F.col("_dlc_int"), F.try_element_at("_payload", F.lit(i))).cast("short").alias(name)
        for i, name in enumerate(PAYLOAD_COLUMNS, start=1)
    ]
    return valid.select(
        "event_timestamp",
        F.col("_can_id_long").cast("int").alias("can_id"),
        F.col("_dlc_int").cast("tinyint").alias("dlc"),
        *byte_cols,
        "source_archive",
        "source_file",
        "source_line",
        "ingested_at",
        "ingestion_run_id",
        F.to_date("event_timestamp").alias("event_date"),
    )


def to_quarantine(parsed: DataFrame) -> DataFrame:
    return parsed.where(F.size("failure_reasons") > 0).select(
        "raw_record",
        "source_archive",
        "source_file",
        "source_line",
        F.try_element_at("failure_reasons", F.lit(1)).alias("failure_reason"),
        "failure_reasons",
        "ingested_at",
        "ingestion_run_id",
    )


def source_summary(parsed: DataFrame) -> DataFrame:
    """Per source file: record / valid / invalid counts, header status, time range."""
    valid = F.size("failure_reasons") == 0
    return parsed.groupBy("source_file").agg(
        F.count(F.lit(1)).alias("record_count"),
        F.sum(valid.cast("long")).alias("valid_record_count"),
        F.sum((~valid).cast("long")).alias("invalid_record_count"),
        F.min("header_ok").alias("header_ok"),
        F.min(F.when(valid, F.col("event_timestamp"))).alias("first_event_timestamp"),
        F.max(F.when(valid, F.col("event_timestamp"))).alias("last_event_timestamp"),
    )
