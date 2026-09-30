"""Gold V1 build (Idle Intelligence): silver_telemetry -> gold tables + evidence objects.

    python -m fleetpulse.pipelines.gold_build --silver-root data/gold_part3/silver --gold-root data/gold_part3/gold

Outputs (full rebuild, published atomically-by-rename): gold_idle_events,
gold_session_summary, gold_operating_summary, gold_idle_evidence/evidence.jsonl,
and _builds/<gold_build_id>.json (config, counts, counter validation).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import functions as F

from fleetpulse.gold import evidence, idle
from fleetpulse.gold.config import IdleRuleConfig
from fleetpulse.gold.storage import ParquetGoldStore
from fleetpulse.silver.storage import SESSIONS, TELEMETRY_TABLE, open_silver_store

GOLD_NAMES = ["gold_idle_events", "gold_session_summary", "gold_operating_summary", "gold_idle_evidence"]


def build_gold(spark, silver_root: Path, gold_root: Path, cfg: IdleRuleConfig = IdleRuleConfig(),
               scope: str = "", storage_format: str = "parquet") -> dict:
    t0 = time.perf_counter()
    build_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    store = ParquetGoldStore(gold_root)
    recovered = store.recover()
    silver = open_silver_store(silver_root, storage_format)
    tele = silver.read(spark, TELEMETRY_TABLE)
    sessions = silver.read(spark, SESSIONS)

    rows = idle.classify(tele, cfg).persist()
    runs = idle.run_table(idle.runs(rows, cfg), cfg).persist()
    events = idle.idle_events(runs, sessions, cfg, build_id).persist()
    summary = idle.session_summary(rows, events, sessions, cfg, build_id).persist()
    operating = idle.operating_summary(summary, cfg, build_id, scope)

    store.write(events, build_id, "gold_idle_events")
    store.write(summary, build_id, "gold_session_summary")
    store.write(operating, build_id, "gold_operating_summary")
    ev_rows = [r.asDict(recursive=True) for r in events.orderBy("event_start").collect()]
    objs = [evidence.evidence_object(e, cfg) for e in evidence.select_important(ev_rows, cfg)]
    store.write_jsonl(objs, build_id, "gold_idle_evidence")

    # Reconciliation: grid-integrated fuel/distance vs the truck's cumulative counters, per session.
    val = summary.select(
        "recording_session_id", "fuel_consumed_l", "fuel_counter_l", "distance_km", "distance_counter_km",
        (F.col("fuel_consumed_l") - F.col("fuel_counter_l")).alias("fuel_diff_l"),
        (F.col("distance_km") - F.col("distance_counter_km")).alias("distance_diff_km"),
    ).orderBy("recording_session_id").collect()
    states = {r.state: r["s"] for r in rows.groupBy("state").agg((F.count(F.lit(1)) * cfg.grid_period_s).alias("s")).collect()}
    op = operating.collect()[0].asDict()
    manifest = {
        "gold_build_id": build_id, "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "silver_root": str(silver_root), "silver_build_ids": sorted({e["silver_build_id"] for e in ev_rows}),
        "scope": scope, "config": cfg.to_dict(), "recovered": recovered,
        "counts": {"idle_events": len(ev_rows), "sessions": summary.count(), "evidence_objects": len(objs)},
        "operating_summary": op,
        "state_seconds": states,
        "counter_validation": [r.asDict() for r in val],
        "runtime_s": round(time.perf_counter() - t0, 1),
    }
    store.publish(build_id, GOLD_NAMES, manifest)
    for d in (rows, runs, events, summary):
        d.unpersist()
    return manifest


def main(argv: list[str] | None = None) -> int:
    from fleetpulse.bronze.spark import get_spark

    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--silver-root", type=Path, required=True)
    p.add_argument("--gold-root", type=Path, required=True)
    p.add_argument("--scope", default="")
    p.add_argument("--min-event-s", type=float, default=IdleRuleConfig.min_event_s)
    p.add_argument("--engine-on-min-rpm", type=float, default=IdleRuleConfig.engine_on_min_rpm)
    p.add_argument("--stationary-max-kph", type=float, default=IdleRuleConfig.stationary_max_kph)
    p.add_argument("--master", default="local[2]")
    p.add_argument("--driver-memory", default="2g")
    a = p.parse_args(argv)
    cfg = IdleRuleConfig(min_event_s=a.min_event_s, engine_on_min_rpm=a.engine_on_min_rpm,
                         stationary_max_kph=a.stationary_max_kph)
    spark = get_spark(app="fleetpulse-gold", master=a.master, driver_memory=a.driver_memory)
    try:
        m = build_gold(spark, a.silver_root, a.gold_root, cfg, a.scope)
    finally:
        spark.stop()
    print(json.dumps({k: m[k] for k in ("gold_build_id", "counts", "operating_summary", "state_seconds", "runtime_s")},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
