"""Descriptive statistics over parsed J1939 frames."""

from __future__ import annotations

import pandas as pd

from fleetpulse.j1939.pgn_catalog import DOCUMENTED_PGNS


def _fmt_hex(values, width: int) -> str:
    return ",".join(f"0x{int(v):0{width}X}" for v in sorted(values))


def pgn_frequency_table(frames: pd.DataFrame, duration_seconds: float | None) -> pd.DataFrame:
    """One row per PGN, sorted by frame count (descending).

    Expects valid frames with columns: pgn, pgn_name, pgn_category, can_id,
    source_address, priority, dlc.
    """
    grouped = frames.groupby("pgn", sort=False)
    table = pd.DataFrame(
        {
            "frame_count": grouped.size(),
            "unique_can_ids": grouped["can_id"].nunique(),
            "can_ids": grouped["can_id"].agg(lambda s: _fmt_hex(s.unique(), 8)),
            "source_addresses": grouped["source_address"].agg(lambda s: _fmt_hex(s.unique(), 2)),
            "priorities": grouped["priority"].agg(lambda s: ",".join(map(str, sorted(s.unique())))),
            "dlcs": grouped["dlc"].agg(lambda s: ",".join(map(str, sorted(s.unique())))),
            "pgn_name": grouped["pgn_name"].first(),
            "pgn_category": grouped["pgn_category"].first(),
        }
    ).reset_index()
    table.insert(1, "pgn_hex", table["pgn"].map(lambda p: f"0x{p:05X}"))
    table["description"] = table["pgn"].map(
        lambda p: DOCUMENTED_PGNS[p][1] if p in DOCUMENTED_PGNS else ""
    )
    table["share_pct"] = 100.0 * table["frame_count"] / table["frame_count"].sum()
    if duration_seconds:
        # Aggregate over all source addresses / CAN IDs carrying this PGN.
        table["mean_rate_hz"] = table["frame_count"] / duration_seconds
    return table.sort_values(["frame_count", "pgn"], ascending=[False, True]).reset_index(drop=True)


def source_address_table(frames: pd.DataFrame) -> pd.DataFrame:
    grouped = frames.groupby("source_address")
    table = pd.DataFrame(
        {
            "frame_count": grouped.size(),
            "unique_pgns": grouped["pgn"].nunique(),
            "pgns": grouped["pgn"].agg(lambda s: ",".join(map(str, sorted(s.unique())))),
        }
    ).reset_index()
    table.insert(1, "source_address_hex", table["source_address"].map(lambda a: f"0x{a:02X}"))
    return table.sort_values("frame_count", ascending=False).reset_index(drop=True)


def describe_signal(values: pd.Series, status: pd.Series) -> dict:
    valid = values[status == "valid"].dropna()
    stats = {
        "frames": int(len(values)),
        "status_counts": {str(k): int(v) for k, v in status.value_counts().items()},
    }
    if valid.empty:
        return stats
    if pd.api.types.is_integer_dtype(values.dtype):  # discrete state codes
        stats["value_counts"] = {int(k): int(v) for k, v in valid.value_counts().sort_index().items()}
        return stats
    stats.update(
        {
            "min": float(valid.min()),
            "max": float(valid.max()),
            "mean": float(valid.mean()),
            "median": float(valid.median()),
            "std": float(valid.std()),
            "p05": float(valid.quantile(0.05)),
            "p95": float(valid.quantile(0.95)),
            "zero_value_frames": int((valid == 0).sum()),
        }
    )
    return stats
