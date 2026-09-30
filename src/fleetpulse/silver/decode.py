"""bronze_can_frames -> silver_signal_events (one row per frame x verified signal).

Only messages/signals in fleetpulse.j1939.definitions.MESSAGES are decoded.
Like the reference ``decode_can_file.decode_message``, a frame of a verified
PGN is decoded only if DLC == 8; other DLCs are counted, not decoded. All
source addresses are kept (the reference does not filter by address).
"""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from fleetpulse.j1939.definitions import MESSAGES
from fleetpulse.silver import j1939
from fleetpulse.silver.schema import SIGNAL_EVENTS

DECODED_PGNS = [m.pgn for m in MESSAGES]


def with_identifier(frames: DataFrame) -> DataFrame:
    can_id = F.col("can_id")
    return frames.select(
        "*",
        j1939.priority(can_id).cast("tinyint").alias("priority"),
        j1939.pgn(can_id).cast("int").alias("pgn"),
        j1939.source_address(can_id).cast("smallint").alias("source_address"),
    )


def excluded_frames(frames_with_id: DataFrame) -> DataFrame:
    """Frames of verified PGNs not decoded because DLC != 8 (per PGN)."""
    return frames_with_id.where(F.col("pgn").isin(DECODED_PGNS) & (F.col("dlc") != 8)).groupBy("pgn").count()


def decode(frames: DataFrame, vehicle_id: str, silver_build_id: str) -> DataFrame:
    """Signal events without recording_session_id (assigned later)."""
    f = with_identifier(frames).where(F.col("pgn").isin(DECODED_PGNS) & (F.col("dlc") == 8))
    # CASE pgn WHEN <pgn> THEN array(<signal structs>) ... - one pass over Bronze.
    per_message = None
    for m in MESSAGES:
        arr = F.array(*[j1939.signal_struct(s, m.key) for s in m.signals])
        cond = F.col("pgn") == m.pgn
        per_message = F.when(cond, arr) if per_message is None else per_message.when(cond, arr)
    exploded = f.select(
        "event_timestamp",
        "can_id",
        "priority",
        "pgn",
        "source_address",
        "source_archive",
        "source_file",
        "source_line",
        "ingestion_run_id",
        F.explode(per_message).alias("sig"),
    )
    cols = [c for c in SIGNAL_EVENTS.fieldNames() if c != "recording_session_id"]
    return exploded.select(
        "*",
        "sig.*",
        F.to_date("event_timestamp").alias("event_date"),
        F.lit(vehicle_id).alias("vehicle_id"),
        F.lit(silver_build_id).alias("silver_build_id"),
    ).select(*cols)


def expected_event_count(frames: DataFrame) -> int:
    """Independent check: decoded frames per PGN x number of signals of that message."""
    counts = {
        r.pgn: r["count"]
        for r in with_identifier(frames)
        .where(F.col("pgn").isin(DECODED_PGNS) & (F.col("dlc") == 8))
        .groupBy("pgn")
        .count()
        .collect()
    }
    return sum(counts.get(m.pgn, 0) * len(m.signals) for m in MESSAGES)
