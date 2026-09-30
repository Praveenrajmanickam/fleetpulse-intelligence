"""Small Delta Lake helpers shared by the Bronze and Silver Delta stores.

Delta Lake here = the open-source ``delta-spark`` library (4.4.0, built for
Spark 4.2 / Scala 2.13, Maven artifact ``io.delta:delta-spark_4.2_2.13``)
running on the local Spark session, writing path-based tables.
"""

from __future__ import annotations

from pathlib import Path

from pyspark.sql.types import StructType


def is_delta_table(path: Path) -> bool:
    return (Path(path) / "_delta_log").is_dir()


def ensure_table(spark, path: Path, schema: StructType, partition_cols: tuple[str, ...] = ()):
    """Create an empty Delta table with an explicit schema if it does not exist.

    Creating the table up front (instead of letting the first append infer it)
    makes the declared contract - column names, types and NOT NULL - the
    table's enforced schema from version 0.
    """
    from delta.tables import DeltaTable

    # Delta resolves a relative location as a table identifier: always pass an absolute path.
    builder = DeltaTable.createIfNotExists(spark).location(str(Path(path).resolve())).addColumns(schema)
    if partition_cols:
        builder = builder.partitionedBy(*partition_cols)
    return builder.execute()


def table(spark, path: Path):
    from delta.tables import DeltaTable

    return DeltaTable.forPath(spark, str(Path(path).resolve()))


def version(spark, path: Path) -> int:
    return int(table(spark, path).history(1).select("version").collect()[0][0])


def last_operation_metrics(spark, path: Path) -> dict:
    row = table(spark, path).history(1).select("version", "operation", "operationMetrics").collect()[0]
    return {"version": int(row.version), "operation": row.operation, **(row.operationMetrics or {})}


def log_stats(path: Path) -> dict:
    """Size of the transaction log on disk (for reporting)."""
    log = Path(path) / "_delta_log"
    files = [p for p in log.rglob("*") if p.is_file()]
    return {
        "log_files": len(files),
        "commit_json_files": len([p for p in files if p.suffix == ".json" and p.parent == log]),
        "log_bytes": sum(p.stat().st_size for p in files),
    }
