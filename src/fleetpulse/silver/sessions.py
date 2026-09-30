"""Recording sessions over the Bronze frame stream (Spark port of validation.continuity).

Semantics are those of the validated reference (fleetpulse.validation.continuity):

* gaps are measured between consecutive valid frames across the complete
  ordered stream: inside each file (logged order = source_line) and between
  files (files ordered by first timestamp);
* a gap strictly greater than ``recording_gap_s`` ends a session;
* overlapping files, filename order != timestamp order, or a backward time
  step larger than the threshold raise ContinuityError;
* sessions describe data continuity, not trips: a parked vehicle that keeps
  broadcasting stays in one session.

Two phases keep it scalable: Spark reduces every file to its "pieces" (runs of
frames between internal breaks; one row per file unless it has breaks), and the
small list of pieces is assembled into sessions on the driver.

Session ids are deterministic (vehicle + start timestamp), so a rebuild from
the same Bronze data reproduces them.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from fleetpulse.validation.continuity import ContinuityError


def ts_micros(col) -> F.Column:
    """TIMESTAMP_NTZ -> epoch microseconds (session time zone must be UTC)."""
    return F.unix_micros(F.col(col).cast("timestamp") if isinstance(col, str) else col.cast("timestamp"))


@dataclass
class Session:
    index: int
    start: object
    end: object
    frames: int
    first_file: str
    last_file: str
    gap_before_s: float | None
    gap_before_kind: str | None
    gap_after_s: float | None = None


def file_pieces(frames: DataFrame, recording_gap_s: float) -> list[dict]:
    """One row per (source_file, piece): frame count, first/last timestamp and line,
    largest non-breaking gap, most negative step, and the break that opened it."""
    thr_us = int(round(recording_gap_s * 1e6))
    w = Window.partitionBy("source_file").orderBy("source_line")
    f = frames.select("source_file", "source_line", "event_timestamp", ts_micros("event_timestamp").alias("ts_us"))
    f = f.select(
        "*",
        (F.col("ts_us") - F.lag("ts_us").over(w)).alias("gap_us"),
        F.lag("event_timestamp").over(w).alias("prev_ts"),
        F.lag("source_line").over(w).alias("prev_line"),
    )
    f = f.select("*", F.coalesce((F.col("gap_us") > thr_us).cast("int"), F.lit(0)).alias("is_break"))
    f = f.select("*", F.sum("is_break").over(w.rowsBetween(Window.unboundedPreceding, 0)).alias("piece"))
    agg = f.groupBy("source_file", "piece").agg(
        F.count(F.lit(1)).alias("frames"),
        F.min_by("event_timestamp", "source_line").alias("first_ts"),
        F.max_by("event_timestamp", "source_line").alias("last_ts"),
        F.min("source_line").alias("first_line"),
        F.max("source_line").alias("last_line"),
        F.max(F.when(F.col("gap_us") <= thr_us, F.col("gap_us"))).alias("max_gap_us"),
        F.min("gap_us").alias("min_gap_us"),
        F.min_by(F.struct("gap_us", "prev_ts", "prev_line"), "source_line").alias("opening"),
    )
    return [r.asDict(recursive=True) for r in agg.orderBy("source_file", "piece").collect()]


def assemble_sessions(pieces: list[dict], recording_gap_s: float) -> tuple[list[Session], list[dict]]:
    """Driver-side assembly (small input). Returns (sessions, breaks)."""
    if not pieces:
        return [], []
    by_file: dict[str, list[dict]] = {}
    for p in pieces:
        by_file.setdefault(p["source_file"], []).append(p)
    for name, ps in by_file.items():
        worst = min((p["min_gap_us"] for p in ps if p["min_gap_us"] is not None), default=0)
        if worst < 0 and -worst / 1e6 > recording_gap_s:
            raise ContinuityError(f"{name}: timestamp jumps back {-worst / 1e6:.3f} s (> {recording_gap_s} s)")
    files = sorted(by_file, key=lambda n: by_file[n][0]["first_ts"])
    if files != sorted(files):
        raise ContinuityError("filename order differs from internal timestamp order")

    sessions: list[Session] = []
    breaks: list[dict] = []
    prev_last, prev_file = None, None
    for name in files:
        for p in sorted(by_file[name], key=lambda p: p["piece"]):
            if p["piece"] == 0 and prev_last is not None:
                gap = (p["first_ts"] - prev_last).total_seconds()
                if gap < 0:
                    raise ContinuityError(f"overlapping files: {prev_file} ends after {name} starts ({gap:.6f} s)")
                opens = gap > recording_gap_s
                kind, before_ts, before_file = "between_files", prev_last, prev_file
            elif p["piece"] > 0:
                gap = p["opening"]["gap_us"] / 1e6
                opens, kind, before_ts, before_file = True, "within_file", p["opening"]["prev_ts"], name
            else:
                opens, gap = True, None  # very first piece
            if opens:
                if gap is not None:
                    breaks.append({"gap_s": gap, "kind": kind, "before_file": before_file,
                                   "before_timestamp": before_ts, "after_file": name,
                                   "after_timestamp": p["first_ts"]})
                    sessions[-1].gap_after_s = gap
                sessions.append(Session(len(sessions), p["first_ts"], p["last_ts"], p["frames"], name, name,
                                        gap, kind if gap is not None else None))
            else:
                s = sessions[-1]
                s.end, s.frames, s.last_file = p["last_ts"], s.frames + p["frames"], name
            prev_last, prev_file = p["last_ts"], name
    return sessions, breaks


def session_id(vehicle_id: str, start) -> str:
    return f"{vehicle_id}-{start:%Y%m%dT%H%M%S%f}"


def session_rows(sessions: list[Session], vehicle_id: str, recording_gap_s: float, build_id: str) -> list[dict]:
    return [
        {
            "vehicle_id": vehicle_id,
            "recording_session_id": session_id(vehicle_id, s.start),
            "session_index": s.index,
            "session_start": s.start,
            "session_end": s.end,
            "duration_s": (s.end - s.start).total_seconds(),
            "frame_count": s.frames,
            "first_source_file": s.first_file,
            "last_source_file": s.last_file,
            "gap_before_s": s.gap_before_s,
            "gap_before_kind": s.gap_before_kind,
            "gap_after_s": s.gap_after_s,
            "recording_gap_threshold_s": float(recording_gap_s),
            "silver_build_id": build_id,
        }
        for s in sessions
    ]


def assign_sessions(df: DataFrame, sessions: DataFrame, ts_col: str = "event_timestamp") -> DataFrame:
    """Attach recording_session_id by time range (sessions are few: broadcast)."""
    s = F.broadcast(sessions.select("recording_session_id", "session_start", "session_end"))
    return df.join(s, (F.col(ts_col) >= F.col("session_start")) & (F.col(ts_col) <= F.col("session_end")), "inner").drop(
        "session_start", "session_end"
    )
