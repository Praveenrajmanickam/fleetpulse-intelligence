"""Recording-session continuity over the complete ordered CAN event stream.

A *recording session* is a stretch of the logged event stream with no
timestamp gap longer than ``recording_gap_s``. Gaps are measured between
consecutive frames everywhere: inside files and across file boundaries. One
file is not assumed to be one continuous recording; the Turku logger can keep
a file open across a long bus silence.

Recording sessions describe the *data*, not the vehicle's use: they are not
trips. Trip detection, if any, is a separate business-level step.

The scan has two phases so it can later run over the full dataset without
holding every timestamp in memory:

1. :func:`summarise_file` (independent per file): within-file gap histogram,
   largest gaps, backward steps, and the file's internal session breaks.
2. :func:`assemble` (needs file order): between-file gaps, session
   segmentation, distribution report.

Structural problems (overlapping files, filename order different from
timestamp order, a backward time step larger than the threshold) raise
:class:`ContinuityError`. Gaps never raise here; the caller decides what to do
with the session breaks.
"""

from __future__ import annotations

import heapq
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

# Reporting bins (seconds): upper edges are inclusive of the lower bound only.
REPORT_EDGES_S = np.array(
    [0, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 300, 900, 3600, np.inf]
)
# Fine log bins (20 per decade, 1 us .. 11.6 days) for approximate quantiles.
FINE_EDGES_S = np.r_[0.0, np.logspace(-6, 6, 241), np.inf]
QUANTILES = (0.5, 0.9, 0.99, 0.999, 0.9999)

# Project-wide default recording-session gap threshold (seconds).
# Calibrated for THIS dataset/logger (Turku part_3, python-can + PCAN-USB), not
# a J1939 rule: the full part_3 gap audit (140,002,800 frames) found every gap
# inside continuous traffic <= 16.6 ms and every recording silence >= 6.3 s, with
# nothing in between; 1 s, 3 s and 5 s all give the same 54 recording sessions.
# 1 s sits well inside that empty band on both sides. Always configurable
# (``--recording-gap-s``); re-audit with pipelines.inspect_gaps for other data.
DEFAULT_RECORDING_GAP_S = 1.0


class ContinuityError(ValueError):
    pass


@dataclass(frozen=True)
class GapEvent:
    gap_s: float
    kind: str  # "within_file" | "between_files"
    before_file: str
    before_timestamp: pd.Timestamp
    before_source_line: int
    after_file: str
    after_timestamp: pd.Timestamp
    after_source_line: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["before_timestamp"] = str(self.before_timestamp)
        d["after_timestamp"] = str(self.after_timestamp)
        return d


@dataclass
class FilePiece:
    """A run of frames in one file that belongs to a single recording session."""

    frames: int
    first_timestamp: pd.Timestamp
    last_timestamp: pd.Timestamp
    max_gap_s: float


@dataclass
class FileGapSummary:
    file: str
    frames: int
    first_timestamp: pd.Timestamp
    last_timestamp: pd.Timestamp
    first_source_line: int
    last_source_line: int
    fine_counts: np.ndarray
    report_counts: np.ndarray
    zero_gaps: int
    backward_steps: int
    max_backward_s: float
    max_gap_s: float
    breaks: list[GapEvent]  # within-file gaps > threshold, in order
    pieces: list[FilePiece]  # len(breaks) + 1
    largest: list[GapEvent] = field(default_factory=list)


def _diff_seconds(ts: np.ndarray) -> np.ndarray:
    # Difference in integer microseconds first: epoch seconds as float64 lose
    # sub-microsecond precision.
    return np.diff(ts.astype("datetime64[us]").astype(np.int64)) / 1e6


def summarise_file(
    file: str,
    timestamps,
    source_lines,
    recording_gap_s: float,
    top_k: int = 10,
) -> FileGapSummary:
    """Summarise gaps inside one file (frames in logged order; NaT dropped)."""
    ts = pd.to_datetime(pd.Series(timestamps)).to_numpy()
    lines = np.asarray(source_lines)
    keep = ~pd.isna(ts)
    ts, lines = ts[keep], lines[keep]
    if len(ts) == 0:
        raise ContinuityError(f"{file}: no valid timestamps")

    diffs = _diff_seconds(ts)
    backward = diffs < 0
    if backward.any() and -diffs[backward].min() > recording_gap_s:
        i = int(np.argmin(diffs))
        raise ContinuityError(
            f"{file}: timestamp jumps back {-diffs[i]:.3f} s at line {lines[i + 1]} "
            f"(> recording gap {recording_gap_s} s); cannot order the stream"
        )
    forward = diffs[~backward]

    def event(i: int) -> GapEvent:
        return GapEvent(
            float(diffs[i]), "within_file",
            file, pd.Timestamp(ts[i]), int(lines[i]),
            file, pd.Timestamp(ts[i + 1]), int(lines[i + 1]),
        )

    break_idx = np.flatnonzero(diffs > recording_gap_s)
    breaks = [event(i) for i in break_idx]

    pieces = []
    starts = np.r_[0, break_idx + 1]
    ends = np.r_[break_idx, len(ts) - 1]
    for s, e in zip(starts, ends):
        inner = diffs[s:e]
        pieces.append(
            FilePiece(
                frames=int(e - s + 1),
                first_timestamp=pd.Timestamp(ts[s]),
                last_timestamp=pd.Timestamp(ts[e]),
                max_gap_s=float(inner.max()) if len(inner) else 0.0,
            )
        )

    k = min(top_k, len(diffs))
    top = np.argpartition(diffs, -k)[-k:] if k else np.array([], dtype=int)
    largest = sorted((event(i) for i in top), key=lambda g: -g.gap_s)

    return FileGapSummary(
        file=file,
        frames=int(len(ts)),
        first_timestamp=pd.Timestamp(ts[0]),
        last_timestamp=pd.Timestamp(ts[-1]),
        first_source_line=int(lines[0]),
        last_source_line=int(lines[-1]),
        fine_counts=np.histogram(forward, FINE_EDGES_S)[0],
        report_counts=np.histogram(forward, REPORT_EDGES_S)[0],
        zero_gaps=int((diffs == 0).sum()),
        backward_steps=int(backward.sum()),
        max_backward_s=float(-diffs[backward].min()) if backward.any() else 0.0,
        max_gap_s=float(diffs.max()) if len(diffs) else 0.0,
        breaks=breaks,
        pieces=pieces,
        largest=largest,
    )


@dataclass
class ContinuityResult:
    recording_gap_s: float
    files: pd.DataFrame
    sessions: pd.DataFrame
    pieces: pd.DataFrame
    breaks: list[GapEvent]
    distribution: pd.DataFrame
    quantiles_s: dict
    largest: list[GapEvent]
    stats: dict

    def to_report(self) -> dict:
        return {
            "recording_gap_s": self.recording_gap_s,
            "stats": self.stats,
            "approx_quantiles_s": self.quantiles_s,
            "quantile_note": "upper edge of a 20-per-decade log bin (<= 12 % relative error)",
            "sessions": _records(self.sessions),
            "breaks": [b.to_dict() for b in self.breaks],
            "largest_gaps": [g.to_dict() for g in self.largest],
            "distribution": _records(self.distribution),
        }


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in r.items()} for r in df.to_dict("records")]


def _bin_labels(edges: np.ndarray) -> list[str]:
    def f(x):
        if np.isinf(x):
            return "inf"
        return f"{x * 1000:g} ms" if x < 1 else f"{x:g} s"

    return [f"[{f(lo)}, {f(hi)})" for lo, hi in zip(edges[:-1], edges[1:])]


def assemble(summaries: list[FileGapSummary], recording_gap_s: float, top_k: int = 20) -> ContinuityResult:
    """Order files, measure between-file gaps and segment recording sessions."""
    if not summaries:
        raise ContinuityError("no files")
    ordered = sorted(summaries, key=lambda s: s.first_timestamp)
    if [s.file for s in ordered] != sorted(s.file for s in ordered):
        raise ContinuityError("filename order differs from internal timestamp order")

    boundary: list[GapEvent] = []
    for prev, nxt in zip(ordered, ordered[1:]):
        gap = (nxt.first_timestamp - prev.last_timestamp).total_seconds()
        if gap < 0:
            raise ContinuityError(f"overlapping files: {prev.file} ends after {nxt.file} starts ({gap:.6f} s)")
        boundary.append(
            GapEvent(
                gap, "between_files",
                prev.file, prev.last_timestamp, prev.last_source_line,
                nxt.file, nxt.first_timestamp, nxt.first_source_line,
            )
        )
    boundary_s = np.array([b.gap_s for b in boundary])

    # Walk the stream in order, cutting sessions at every gap > threshold.
    breaks: list[GapEvent] = []
    piece_rows = []
    session = 0
    for i, s in enumerate(ordered):
        if i > 0 and boundary[i - 1].gap_s > recording_gap_s:
            breaks.append(boundary[i - 1])
            session += 1
        for j, p in enumerate(s.pieces):
            if j > 0:
                breaks.append(s.breaks[j - 1])
                session += 1
            piece_rows.append(
                {
                    "session_index": session,
                    "file": s.file,
                    "frames": p.frames,
                    "first_timestamp": p.first_timestamp,
                    "last_timestamp": p.last_timestamp,
                    "max_gap_s": p.max_gap_s,
                    "gap_before_s": boundary[i - 1].gap_s if (i > 0 and j == 0) else None,
                }
            )
    pieces = pd.DataFrame(piece_rows)

    sessions = []
    for idx, g in pieces.groupby("session_index", sort=True):
        # Boundary gaps inside a session (not the one that opened it).
        inner_boundary = g["gap_before_s"].iloc[1:].dropna()
        start, end = g["first_timestamp"].iloc[0], g["last_timestamp"].iloc[-1]
        sessions.append(
            {
                "session_index": int(idx),
                "start": start,
                "end": end,
                "duration_s": (end - start).total_seconds(),
                "frames": int(g["frames"].sum()),
                "n_files": int(g["file"].nunique()),
                "first_file": g["file"].iloc[0],
                "last_file": g["file"].iloc[-1],
                "max_gap_s": float(max(g["max_gap_s"].max(), inner_boundary.max() if len(inner_boundary) else 0.0)),
                "gap_before_s": breaks[idx - 1].gap_s if idx > 0 else None,
                "gap_after_s": breaks[idx].gap_s if idx < len(breaks) else None,
            }
        )
    sessions = pd.DataFrame(sessions)

    within_fine = np.sum([s.fine_counts for s in ordered], axis=0)
    between_fine = np.histogram(boundary_s, FINE_EDGES_S)[0]
    total_fine = within_fine + between_fine
    cum = np.cumsum(total_fine) / max(total_fine.sum(), 1)
    all_max = max([s.max_gap_s for s in ordered] + ([boundary_s.max()] if len(boundary_s) else []))
    quantiles = {
        f"p{q * 100:g}": float(min(FINE_EDGES_S[np.searchsorted(cum, q) + 1], all_max)) for q in QUANTILES
    }
    quantiles["max"] = float(all_max)

    within_rep = np.sum([s.report_counts for s in ordered], axis=0)
    between_rep = np.histogram(boundary_s, REPORT_EDGES_S)[0]
    distribution = pd.DataFrame(
        {
            "bin_low_s": REPORT_EDGES_S[:-1],
            "bin_high_s": REPORT_EDGES_S[1:],
            "bin": _bin_labels(REPORT_EDGES_S),
            "within_file": within_rep,
            "between_files": between_rep,
        }
    )
    distribution["total"] = distribution["within_file"] + distribution["between_files"]
    distribution["above_threshold"] = distribution["bin_low_s"] >= recording_gap_s

    largest = heapq.nlargest(top_k, [g for s in ordered for g in s.largest] + boundary, key=lambda g: g.gap_s)

    files = pd.DataFrame(
        {
            "file": [s.file for s in ordered],
            "frames": [s.frames for s in ordered],
            "first_timestamp": [s.first_timestamp for s in ordered],
            "last_timestamp": [s.last_timestamp for s in ordered],
            "max_internal_gap_s": [s.max_gap_s for s in ordered],
            "internal_breaks": [len(s.breaks) for s in ordered],
            "backward_steps": [s.backward_steps for s in ordered],
            "gap_to_next_s": list(boundary_s) + [None],
        }
    )
    n_gaps = int(total_fine.sum()) + sum(s.backward_steps for s in ordered)
    stats = {
        "files": len(ordered),
        "frames": int(sum(s.frames for s in ordered)),
        "gaps_measured": n_gaps,
        "within_file_gaps": int(within_fine.sum()),
        "between_file_gaps": int(len(boundary_s)),
        "zero_gaps": int(sum(s.zero_gaps for s in ordered)),
        "backward_steps": int(sum(s.backward_steps for s in ordered)),
        "max_backward_s": float(max(s.max_backward_s for s in ordered)),
        "gaps_above_threshold": len(breaks),
        "recording_sessions": len(sessions),
        "max_gap_s": float(all_max),
    }
    return ContinuityResult(
        recording_gap_s=recording_gap_s,
        files=files,
        sessions=sessions,
        pieces=pieces,
        breaks=breaks,
        distribution=distribution,
        quantiles_s=quantiles,
        largest=largest,
        stats=stats,
    )


def write_report(result: ContinuityResult, out_dir) -> dict:
    """Write the gap/segmentation report files; returns their paths."""
    import json
    from pathlib import Path

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "gap_report": out_dir / "gap_report.json",
        "gap_distribution": out_dir / "gap_distribution.csv",
        "recording_sessions": out_dir / "recording_sessions.csv",
        "session_breaks": out_dir / "session_breaks.csv",
        "largest_gaps": out_dir / "largest_gaps.csv",
        "files": out_dir / "files.csv",
    }
    paths["gap_report"].write_text(json.dumps(result.to_report(), indent=2, default=str))
    result.distribution.to_csv(paths["gap_distribution"], index=False)
    result.sessions.to_csv(paths["recording_sessions"], index=False)
    pd.DataFrame([b.to_dict() for b in result.breaks], columns=list(GapEvent.__dataclass_fields__)).to_csv(
        paths["session_breaks"], index=False
    )
    pd.DataFrame([g.to_dict() for g in result.largest]).to_csv(paths["largest_gaps"], index=False)
    result.files.to_csv(paths["files"], index=False)
    return {k: str(v) for k, v in paths.items()}
