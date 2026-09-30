"""Align multi-rate decoded messages onto one regular time grid.

Strategy
--------
* Grid: fixed ``period_ms`` (default 100 ms = 10 Hz, the FMS repetition rate
  of CCVS, the slowest verified message). No signal is upsampled beyond the
  rate of its slowest source by more than the grid period.
* Value at grid time ``t``: the most recent frame of that message with
  timestamp <= t (backward as-of join, no look-ahead, no interpolation).
  Faster messages (EEC1 at 20 ms) are therefore point-sampled, not averaged.
* Staleness: a frame is only used if ``t - frame_time <= max_age``, with
  ``max_age = MAX_AGE_FACTOR x nominal FMS repetition period`` of the message
  (EEC1 60 ms, EEC2 150 ms, CCVS 300 ms). Older data leaves the cell empty,
  so a dropout shows up as missing data instead of being forward-filled.
* A frame whose signal status is not ``valid`` yields an empty value; we do
  not fall back to an older valid frame.
* ``<msg>_age_ms`` records how old the sample used for each grid row is.

Only signals from :mod:`fleetpulse.j1939.definitions` are carried over.
"""

from __future__ import annotations

import logging

import pandas as pd

from fleetpulse.j1939.definitions import MESSAGES, MessageSpec

log = logging.getLogger(__name__)

DEFAULT_PERIOD_MS = 100
MAX_AGE_FACTOR = 3.0


class AmbiguousSourceError(ValueError):
    """A message was received from more than one source address."""


def max_age_ms(message: MessageSpec) -> float:
    return MAX_AGE_FACTOR * message.nominal_period_ms


def make_grid(start: pd.Timestamp, end: pd.Timestamp, period_ms: int = DEFAULT_PERIOD_MS) -> pd.DatetimeIndex:
    freq = pd.Timedelta(milliseconds=period_ms)
    return pd.date_range(start.ceil(freq), end.floor(freq), freq=freq, name="timestamp")


def align_message(grid: pd.DatetimeIndex, table: pd.DataFrame, message: MessageSpec) -> pd.DataFrame:
    """As-of join one decoded message table onto ``grid``."""
    sources = table["source_address"].dropna().unique()
    if len(sources) > 1:
        raise AmbiguousSourceError(
            f"{message.acronym} received from {len(sources)} source addresses "
            f"{sorted(int(s) for s in sources)}; choose one explicitly"
        )
    cols = [s.name for s in message.signals]
    right = (
        table[["timestamp", *cols]]
        .rename(columns={"timestamp": f"{message.key}_sample_time"})
        .sort_values(f"{message.key}_sample_time")
    )
    left = pd.DataFrame({"timestamp": grid})
    merged = pd.merge_asof(
        left,
        right,
        left_on="timestamp",
        right_on=f"{message.key}_sample_time",
        direction="backward",
        tolerance=pd.Timedelta(milliseconds=max_age_ms(message)),
        allow_exact_matches=True,
    )
    age = (merged["timestamp"] - merged[f"{message.key}_sample_time"]).dt.total_seconds() * 1e3
    merged[f"{message.key}_age_ms"] = age
    return merged.drop(columns=[f"{message.key}_sample_time"]).set_index("timestamp")


def build_telemetry(
    tables: dict[str, pd.DataFrame],
    period_ms: int = DEFAULT_PERIOD_MS,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Build the aligned telemetry table from per-message decoded tables.

    ``tables`` maps MessageSpec.key -> decoded table (as written by
    decode_can_file). The grid spans the overlap of all messages unless
    ``start``/``end`` are given.
    """
    messages = [m for m in MESSAGES if m.key in tables]
    if start is None:
        start = max(tables[m.key]["timestamp"].min() for m in messages)
    if end is None:
        end = min(tables[m.key]["timestamp"].max() for m in messages)
    grid = make_grid(start, end, period_ms)
    log.info("Telemetry grid: %d rows at %d ms from %s to %s", len(grid), period_ms, grid[0], grid[-1])

    parts = [align_message(grid, tables[m.key], m) for m in messages]
    telemetry = pd.concat(parts, axis=1).reset_index()
    for m in messages:
        n_missing = telemetry[f"{m.key}_age_ms"].isna().sum()
        if n_missing:
            log.warning("%s: %d grid rows have no sample within %.0f ms", m.acronym, n_missing, max_age_ms(m))
    return telemetry


def alignment_config(period_ms: int = DEFAULT_PERIOD_MS) -> dict:
    return {
        "grid_period_ms": period_ms,
        "method": "backward as-of join (last observed frame at or before grid time); "
        "no interpolation, no look-ahead",
        "max_age_rule": f"{MAX_AGE_FACTOR:g} x FMS nominal repetition period",
        "max_age_ms": {m.acronym: max_age_ms(m) for m in MESSAGES},
        "invalid_status_handling": "value left empty; no fallback to older valid frames",
    }
