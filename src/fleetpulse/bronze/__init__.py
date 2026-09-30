"""Bronze layer: one row per raw CAN frame, source-faithful, no J1939 decoding.

Module boundaries (so storage can move to Delta Lake without touching parsing):

* ``archive_source`` - reads tar(.xz) members one at a time, checksums them,
  stages them as plain files for Spark. No Spark.
* ``parsing`` - pure Spark transformations: raw file text -> parsed frames ->
  (valid, quarantine). No I/O decisions.
* ``ingestion_log`` - per-source decisions (ingest / skip / conflict) from the
  committed ingestion log. Pure Python.
* ``storage`` - where and how tables are written and committed
  (``ParquetBronzeStore`` today; a Delta store later).
* ``pipelines.bronze_ingest`` - orchestration and CLI.
"""
