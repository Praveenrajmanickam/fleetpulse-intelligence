"""Read-only access to Gold (and Silver telemetry for timelines)."""

from __future__ import annotations

import json
import math
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

TIMELINE_COLUMNS = ["grid_timestamp", "wheel_speed_kph", "engine_speed_rpm", "fuel_rate_lph", "coolant_temp_c",
                    "engine_load_pct"]


def clean(value):
    """JSON-safe scalar: NaN -> None, timestamps -> ISO string (no timezone), numpy -> python."""
    if value is None:
        return None
    if isinstance(value, (float, np.floating)):
        return None if math.isnan(value) else float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.ndarray):
        return [clean(v) for v in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: clean(v) for k, v in r.items()} for r in df.to_dict(orient="records")]


class GoldRepository:
    def __init__(self, gold_root: Path, silver_root: Path | None = None):
        self.gold_root = Path(gold_root)
        self.silver_root = Path(silver_root) if silver_root else None

    @cached_property
    def manifest(self) -> dict:
        builds = sorted((self.gold_root / "_builds").glob("*.json"))
        if not builds:
            raise FileNotFoundError(f"no Gold build manifest under {self.gold_root}")
        return json.loads(builds[-1].read_text())

    @cached_property
    def operating(self) -> dict:
        df = pq.read_table(self.gold_root / "gold_operating_summary").to_pandas()
        return records(df)[0]

    @cached_property
    def sessions(self) -> pd.DataFrame:
        return pq.read_table(self.gold_root / "gold_session_summary").to_pandas().sort_values("session_start").reset_index(drop=True)

    @cached_property
    def events(self) -> pd.DataFrame:
        return pq.read_table(self.gold_root / "gold_idle_events").to_pandas().sort_values("event_start").reset_index(drop=True)

    @cached_property
    def evidence(self) -> dict[str, dict]:
        p = self.gold_root / "gold_idle_evidence" / "evidence.jsonl"
        if not p.exists():
            return {}
        return {o["evidence_id"]: o for o in (json.loads(line) for line in p.read_text().splitlines() if line)}

    def build_info(self) -> dict:
        m = self.manifest
        return {"gold_build_id": m["gold_build_id"], "built_at_utc": m["built_at_utc"],
                "rule_version": m["config"]["rule_version"], "silver_build_ids": m.get("silver_build_ids", []),
                "rule_config": m["config"]}

    def event(self, event_id: str) -> dict | None:
        row = self.events[self.events["event_id"] == event_id]
        return records(row)[0] if len(row) else None

    def session(self, session_id: str) -> dict | None:
        row = self.sessions[self.sessions["recording_session_id"] == session_id]
        return records(row)[0] if len(row) else None

    def timeline(self, session_id: str, start: pd.Timestamp, end: pd.Timestamp, max_points: int = 1500) -> dict:
        """Silver telemetry for one session and time window, display-sampled (every k-th row,
        no aggregation). Nulls stay null (drawn as gaps)."""
        if self.silver_root is None:
            return {"available": False, "points": []}
        dataset = ds.dataset(self.silver_root / "silver_telemetry", format="parquet", partitioning="hive")
        flt = ((pc.field("recording_session_id") == session_id) & (pc.field("grid_timestamp") >= start.to_datetime64())
               & (pc.field("grid_timestamp") <= end.to_datetime64()))
        df = dataset.to_table(filter=flt, columns=TIMELINE_COLUMNS).to_pandas().sort_values("grid_timestamp")
        step = max(1, math.ceil(len(df) / max_points))
        sampled = df.iloc[::step]
        return {"available": True, "source": "silver_telemetry (100 ms grid)", "sampling_step_rows": step,
                "sampling_note": "every k-th 100 ms grid row for display; values are not averaged",
                "points": records(sampled.rename(columns={"grid_timestamp": "t"}))}
