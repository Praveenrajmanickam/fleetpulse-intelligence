"""Row-level and file-level data-quality checks for raw CAN frames."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from fleetpulse.ingestion.can_csv import BYTE_COLUMNS, MAX_DLC
from fleetpulse.j1939.identifier import MAX_29BIT_ID

log = logging.getLogger(__name__)

LARGE_GAP_SECONDS = 1.0


@dataclass
class FrameQualityReport:
    total_rows: int
    valid_rows: int
    invalid_rows: int
    issue_counts: dict[str, int] = field(default_factory=dict)
    timestamp_min: str | None = None
    timestamp_max: str | None = None
    duration_seconds: float | None = None
    non_monotonic_timestamps: int = 0
    max_backward_step_seconds: float = 0.0
    duplicate_frames: int = 0
    gaps_over_threshold: int = 0
    gap_threshold_seconds: float = LARGE_GAP_SECONDS
    max_gap_seconds: float = 0.0
    dlc_distribution: dict[int, int] = field(default_factory=dict)
    can_ids_with_inconsistent_dlc: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def flag_invalid_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``is_valid`` and ``quality_issue`` columns (first issue wins)."""
    bytes_block = df[BYTE_COLUMNS]
    out_of_range = ((bytes_block < 0) | (bytes_block > 255)).fillna(False).any(axis=1)
    checks = [
        ("bad_timestamp", df["timestamp"].isna()),
        ("bad_can_id", df["can_id"].isna()),
        ("can_id_exceeds_29_bits", (df["can_id"] > MAX_29BIT_ID).fillna(False)),
        ("bad_dlc", df["dlc"].isna() | ~df["dlc"].between(0, MAX_DLC).fillna(False)),
        ("dlc_byte_count_mismatch", (df["n_bytes"] != df["dlc"]).fillna(True)),
        ("byte_out_of_range", out_of_range),
    ]
    issue = np.full(len(df), "", dtype=object)
    for name, mask in reversed(checks):  # reversed so the first check wins
        issue = np.where(mask.to_numpy(dtype=bool), name, issue)
    df = df.copy()
    df["quality_issue"] = pd.array(issue, dtype="string")
    df["is_valid"] = df["quality_issue"] == ""
    return df


def build_quality_report(df: pd.DataFrame) -> FrameQualityReport:
    """File-level summary. Expects the output of :func:`flag_invalid_rows`."""
    issues = df.loc[~df["is_valid"], "quality_issue"].value_counts()
    report = FrameQualityReport(
        total_rows=len(df),
        valid_rows=int(df["is_valid"].sum()),
        invalid_rows=int((~df["is_valid"]).sum()),
        issue_counts={str(k): int(v) for k, v in issues.items()},
    )

    ts = df["timestamp"].dropna()
    if not ts.empty:
        report.timestamp_min = str(ts.min())
        report.timestamp_max = str(ts.max())
        report.duration_seconds = (ts.max() - ts.min()).total_seconds()
        # File order is the logger's order; measure against it, not sorted order.
        step = ts.diff().dt.total_seconds().dropna()
        backwards = step[step < 0]
        report.non_monotonic_timestamps = int(len(backwards))
        report.max_backward_step_seconds = float(-backwards.min()) if len(backwards) else 0.0
        sorted_gaps = ts.sort_values().diff().dt.total_seconds().dropna()
        report.gaps_over_threshold = int((sorted_gaps > LARGE_GAP_SECONDS).sum())
        report.max_gap_seconds = float(sorted_gaps.max()) if len(sorted_gaps) else 0.0

    report.duplicate_frames = int(
        df.duplicated(subset=["timestamp", "can_id", "dlc", *BYTE_COLUMNS]).sum()
    )
    report.dlc_distribution = {
        int(k): int(v) for k, v in df["dlc"].value_counts(dropna=True).sort_index().items()
    }
    dlc_per_id = df.dropna(subset=["can_id"]).groupby("can_id")["dlc"].nunique()
    report.can_ids_with_inconsistent_dlc = [
        f"0x{int(i):08X}" for i in dlc_per_id[dlc_per_id > 1].index
    ]

    for name, count in report.issue_counts.items():
        log.warning("Quality issue %s: %d rows", name, count)
    if report.non_monotonic_timestamps:
        log.warning(
            "%d timestamps go backwards (max %.6f s)",
            report.non_monotonic_timestamps,
            report.max_backward_step_seconds,
        )
    if report.duplicate_frames:
        log.warning("%d exact duplicate frames", report.duplicate_frames)
    if report.can_ids_with_inconsistent_dlc:
        log.warning("CAN IDs with inconsistent DLC: %s", report.can_ids_with_inconsistent_dlc)
    return report
