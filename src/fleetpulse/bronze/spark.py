"""Local SparkSession for Bronze/Silver development (Delta-capable).

The session always carries the Delta Lake jars and SQL extension. PySpark
starts one JVM per Python process and fixes its classpath at launch, so a
session created later in the same process (e.g. in a test run) could not add
Delta afterwards. Being Delta-capable does not change what is written: the
storage format is chosen per store (Parquet remains the default baseline).
"""

from __future__ import annotations

from pyspark.sql import SparkSession


def get_spark(
    app: str = "fleetpulse-bronze",
    master: str = "local[4]",
    driver_memory: str = "2g",
    shuffle_partitions: int = 8,
    extra_conf: dict | None = None,
) -> SparkSession:
    """``extra_conf``: additional Spark settings (e.g. event logging for benchmarks)."""
    from delta import configure_spark_with_delta_pip

    builder = (
        SparkSession.builder.appName(app)
        .master(master)
        .config("spark.driver.memory", driver_memory)
        .config("spark.sql.shuffle.partitions", shuffle_partitions)
        # Source timestamps are timezone-less; nothing may depend on the host timezone.
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.driver.extraJavaOptions", "-Duser.timezone=UTC")
        .config("spark.executor.extraJavaOptions", "-Duser.timezone=UTC")
        .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
        # No pandas interop in Bronze (PySpark 4.2 does not fully support pandas 3).
        .config("spark.sql.execution.arrow.pyspark.enabled", "false")
        .config("spark.hadoop.mapreduce.fileoutputcommitter.marksuccessfuljobs", "false")
        .config("spark.ui.enabled", "false")
        # Delta Lake (delta-spark 4.4.0): SQL extension + catalog for DELETE/time travel etc.
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    )
    for k, v in (extra_conf or {}).items():
        builder = builder.config(k, v)
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    return spark
