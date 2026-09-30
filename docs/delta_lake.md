# Delta Lake in FleetPulse (local)

Delta Lake is a second storage implementation behind the existing store interface. The Bronze and Silver **contracts, parsing, decoding and alignment code are unchanged**. Parquet stays the validated baseline and the default (`--format parquet`).

```
python -m fleetpulse.pipelines.bronze_ingest --format delta --archive data/raw/part_3.tar.xz \
    --include-list config/bronze_subset_part3.txt --root data/delta/bronze
python -m fleetpulse.pipelines.silver_build  --format delta --bronze-root data/delta/bronze --silver-root data/delta/silver
python -m fleetpulse.pipelines.delta_parity    # Delta vs Parquet, row by row
```

## Versions

- **Installed:** `delta-spark` **4.4.0**, whose Python package declares `pyspark>=4.0.1,<=4.2.0`. It loads the JVM library `io.delta:delta-spark_4.2_2.13:4.4.0`, built for Spark 4.2 / Scala 2.13, matching the installed PySpark 4.2.0.
- **Rejected:** delta-spark 4.2.0 and 4.3.x would have downgraded PySpark to 4.1.1.
- **Nothing upgraded.** The only other additions are `importlib_metadata` and `zipp`.
- **Jar download.** The Delta jars are fetched once from Maven Central into the local Ivy cache on the first Spark start (about 1 minute).
- **Spark session.** The shared session (`bronze/spark.py`) always carries the Delta jars and SQL extension. PySpark fixes the JVM classpath when the first session starts in a process, so Delta cannot be added later. The *format* is chosen per store (`open_bronze_store` / `open_silver_store`).

## What problem Delta solves here

The Parquet stores had to **emulate** transactions: write to staging, move files with `os.replace`, treat the log file as the commit marker, and clean up orphans on the next run. That emulation has known gaps:
- a reader during step 2 can see a half-committed batch;
- a stray file in a table directory is read as data;
- concurrent writers are unsafe;
- there is no history.

Delta moves correctness into the storage format itself.

**Transaction log.**
- Each table has a `_delta_log/` of ordered JSON commits (`000…0.json`, `000…1.json`, …) and periodic checkpoints.
- A commit lists the data files it **adds** and **removes**, plus the schema, the table protocol and statistics.
- The table *is* the log replayed up to a version; data files not referenced by a committed version are not part of the table.
- Tested: a stray Parquet file copied into a partition is ignored.

**ACID, per table.**
- **Atomicity:** a write becomes visible only when its commit JSON is written atomically as the next version. A failed Spark job commits nothing, so readers never see partial writes.
- **Isolation:** optimistic concurrency. Readers see a consistent snapshot, and conflicting concurrent writers are detected at commit time.
- **Durability:** anything committed stays in the log.
- **Scope:** transactions are **single-table**. Delta has no transactions spanning several tables, so cross-table consistency is handled by the protocols below.

**Schema enforcement.**
- Tables are created up front from the explicit contract schemas (`DeltaTable.createIfNotExists(...).addColumns(schema)`). Names, types and `NOT NULL` (as Delta invariants) are enforced from version 0.
- Tested rejections: an extra column, a wrong type, a NULL in a NOT NULL lineage column, and a schema-changing Silver overwrite. `mergeSchema` / `overwriteSchema` are never set, so schema changes must be deliberate.
- Table features in use: `timestampNtz` (Bronze and Silver timestamps are timezone-less) and `invariants`.

## Idempotency with Delta

**Bronze (incremental, append).** Ingest / skip / conflict decisions still come from `bronze_ingestion_log`, which is now a Delta table. A batch touches three tables, so the commit protocol is:
1. Append `pending` log rows (a write-ahead intent) for the sources being ingested.
2. Append frames, then quarantine. Each is one atomic commit with `txnAppId=<commit_id>`, `txnVersion=0`: if the same commit is replayed, Delta skips it instead of duplicating rows (tested).
3. Append the final log rows. **This is the commit point.**

`recover()` at the start of every run handles a crash between steps 1 and 3:
- It finds `pending` intents without a final row.
- It deletes exactly those sources' rows *of that run* with a Delta `DELETE` (itself a transaction).
- It records `rolled_back`.

Tested: a crash after the data commit is rolled back on rerun, and the source is re-ingested with no duplicates. Ingest decisions count only `ingested` rows, so `pending` / `rolled_back` never change what is skipped.

**Silver (full deterministic rebuild).**
- Each table is replaced with one `mode("overwrite")` commit; the old version stays in the log for time travel.
- The build manifest `_builds/<build_id>.json` is written last and pins the Delta version of every table in that build.
- `DeltaSilverStore.read()` reads the manifest-pinned versions, so a build that fails half-way (tested: events overwritten, telemetry not) is **never** seen as a mixed snapshot.
- Rebuilding from unchanged Bronze gives identical rows (tested: version *n* vs *n+1*, differing only in `silver_build_id`).

## Partitioning

Unchanged:
- `bronze_can_frames`, `silver_signal_events` and `silver_telemetry` are partitioned by `event_date`; the other tables are unpartitioned.
- Same sort order within files.
- Same zstd compression as the Parquet stores.

## Operational notes

- **Old versions are kept** (Silver rebuilds, rolled-back Bronze rows) until `VACUUM`. That is why Silver shows two data files per date after two builds.
- **Small files.** Each Bronze commit adds at least one file per event date it touches; `OPTIMIZE` compacts them. Not needed at subset scale.
- **Performance overhead.** On the subset, Delta costs more time (log commits, per-file statistics, table creation) than plain Parquet. Delta's benefits are correctness, concurrency and history, not speed on 400 k rows.

## Why this prepares for Databricks

- **Native format.** Delta Lake is Databricks' native table format; the same `_delta_log` protocol is read by Databricks, Spark on Azure, and non-Spark readers such as delta-rs.
- **Minimal change to move.** Moving to Unity Catalog or ADLS means changing table *locations* and names, not the protocol or the code that produces the data.
- **Cloud-ready guarantees.** ACID commits on object storage, schema enforcement, time travel for audit and reprocessing, `OPTIMIZE`/`VACUUM`, and later `MERGE`/`replaceWhere` for incremental Silver builds all become available.

## Known limitations

- **No multi-table atomicity** (a Delta property, handled by the intent log and the manifest above).
- **Path-based tables only**: no metastore/catalog locally.
- **Concurrent Bronze writers** are still not a supported use case: the intent protocol assumes one ingestion job at a time.
- **Recovery cost.** Bronze recovery `DELETE` rewrites the affected files; this is cheap for a batch, but a full-table scan would be avoided on cloud by keeping batches small.
