"""Delta Lake storage for Bronze and Silver: ACID log, schema enforcement, idempotency, parity."""

import datetime
import tarfile
from pathlib import Path

import pytest

pytest.importorskip("delta")

from test_bronze import L_DLC0, L_DLC1, L_DLC4, L_DLC8, L_WHOLE_SECOND, csv, make_archive  # noqa: E402

from fleetpulse import delta_support  # noqa: E402
from fleetpulse.bronze.ingestion_log import SourceConflictError  # noqa: E402
from fleetpulse.bronze.schema import BRONZE_CAN_FRAMES  # noqa: E402
from fleetpulse.bronze.storage import open_bronze_store  # noqa: E402
from fleetpulse.bronze.storage_delta import DeltaBronzeStore  # noqa: E402
from fleetpulse.pipelines.bronze_ingest import BronzeIngestion  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    from fleetpulse.bronze.spark import get_spark

    s = get_spark(app="delta-tests", master="local[2]", driver_memory="1g", shuffle_partitions=2)
    yield s
    s.stop()


MEMBERS = {
    "p/a.csv": csv(L_DLC8, L_DLC4, "garbage", L_DLC1),
    "p/b.csv": csv(L_DLC0, L_WHOLE_SECOND),
}


def ingest(spark, tmp_path, members=MEMBERS, fmt="delta", root=None, archive="part_x.tar.xz", **kw):
    arch = make_archive(tmp_path / archive, members)
    store = open_bronze_store(root or tmp_path / f"bronze_{fmt}", fmt)
    return store, BronzeIngestion(spark, store, **kw).run(arch)


def test_bronze_tables_are_delta_with_transaction_log(spark, tmp_path):
    store, _ = ingest(spark, tmp_path)
    for path in (store.frames_dir, store.quarantine_dir, store.log_dir):
        assert delta_support.is_delta_table(path)
        assert (path / "_delta_log" / "00000000000000000000.json").exists()
    ops = [r.operation for r in delta_support.table(spark, store.frames_dir).history().collect()]
    assert ops[-1] == "CREATE TABLE" and "WRITE" in ops
    detail = delta_support.table(spark, store.frames_dir).detail().collect()[0]
    assert detail.partitionColumns == ["event_date"]  # unchanged partition strategy
    assert {"timestampNtz", "invariants"} <= set(detail.tableFeatures)
    assert store.read_frames(spark).schema.fieldNames() == BRONZE_CAN_FRAMES.fieldNames()


def test_same_subset_twice_no_duplicate_frames(spark, tmp_path):
    store, s1 = ingest(spark, tmp_path)
    v_frames = delta_support.version(spark, store.frames_dir)
    store, s2 = ingest(spark, tmp_path)
    assert (s1["sources"]["ingested"], s2["sources"]["skipped_already_ingested"], s2["records"]) == (2, 2, 0)
    f = store.read_frames(spark)
    assert f.count() == 5 == f.select("source_file", "source_line").distinct().count()
    assert delta_support.version(spark, store.frames_dir) == v_frames  # rerun wrote no frames commit
    assert store.read_quarantine(spark).count() == 1


def test_changed_checksum_is_a_conflict(spark, tmp_path):
    root = tmp_path / "bronze"
    ingest(spark, tmp_path / "v1", root=root)
    changed = {**MEMBERS, "p/a.csv": csv(L_DLC8)}
    with pytest.raises(SourceConflictError):
        ingest(spark, tmp_path / "v2", changed, root=root)
    store = DeltaBronzeStore(root)
    statuses = [r.status for r in store.read_log(spark).collect()]
    assert statuses.count("conflict_checksum_changed") == 1
    assert store.read_frames(spark).where("source_file = 'p/a.csv'").count() == 3  # v1 rows only


def test_schema_incompatible_writes_are_rejected(spark, tmp_path):
    from pyspark.errors import AnalysisException
    from pyspark.sql import functions as F

    store, _ = ingest(spark, tmp_path)
    path = str(store.frames_dir)
    v0, n0 = delta_support.version(spark, store.frames_dir), store.read_frames(spark).count()
    good = store.read_frames(spark).limit(1)
    extra_column = good.withColumn("engine_speed_rpm", F.lit(600.25))
    wrong_type = good.withColumn("can_id", F.lit("0xcf004e6"))
    with pytest.raises(AnalysisException):
        extra_column.write.format("delta").mode("append").save(path)
    with pytest.raises(AnalysisException):
        wrong_type.write.format("delta").mode("append").save(path)
    null_lineage = good.withColumn("source_file", F.lit(None).cast("string"))
    with pytest.raises(Exception, match="(?i)null|invariant|DELTA_NOT_NULL"):
        null_lineage.write.format("delta").mode("append").save(path)
    assert delta_support.version(spark, store.frames_dir) == v0  # no commit from any failed write
    assert store.read_frames(spark).count() == n0


def test_files_not_in_the_log_are_invisible(spark, tmp_path):
    """ACID: data files written without a committed log entry are not part of the table."""
    store, _ = ingest(spark, tmp_path)
    n = store.read_frames(spark).count()
    (existing,) = list((store.frames_dir / "event_date=2021-01-19").glob("*.parquet"))[:1]
    stray = existing.parent / "part-99999-uncommitted.snappy.parquet"
    stray.write_bytes(existing.read_bytes())
    assert store.read_frames(spark).count() == n  # a Parquet directory read would double-count


def test_interrupted_commit_is_rolled_back_on_rerun(spark, tmp_path, monkeypatch):
    calls = {"n": 0}
    original = DeltaBronzeStore._append_final_log

    def crash_once(self, spark_, rows, commit_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash after data commit, before final log")
        return original(self, spark_, rows, commit_id)

    monkeypatch.setattr(DeltaBronzeStore, "_append_final_log", crash_once)
    root = tmp_path / "bronze"
    with pytest.raises(RuntimeError, match="simulated crash"):
        ingest(spark, tmp_path, root=root)
    store = DeltaBronzeStore(root)
    assert store.read_frames(spark).count() == 5  # data committed, but no final log row
    assert [r.status for r in store.read_log(spark).collect()] == ["pending", "pending"]

    _, s = ingest(spark, tmp_path, root=root)
    assert s["recovered"]["rolled_back_sources"] == 2 and s["recovered"]["deleted_frames"] == 5
    f = store.read_frames(spark)
    assert f.count() == 5 == f.select("source_file", "source_line").distinct().count()
    statuses = sorted(r.status for r in store.read_log(spark).collect())
    # every commit writes its intents first: 2 from the crashed commit (rolled back) + 2 from the good one
    assert statuses == ["ingested", "ingested", "pending", "pending", "pending", "pending", "rolled_back", "rolled_back"]
    assert {r.commit_id for r in store.read_log(spark).where("status = 'ingested'").collect()} == store.committed_commit_ids() - {
        r.commit_id for r in store.read_log(spark).where("status = 'rolled_back'").collect()}


def test_replayed_commit_is_idempotent(spark, tmp_path):
    store, _ = ingest(spark, tmp_path)
    frames = store.read_frames(spark)
    n = frames.count()
    row = {
        "source_archive": "x", "source_file": "p/z.csv", "source_sha256": "0" * 64, "source_size_bytes": 1,
        "header_ok": True, "record_count": n, "valid_record_count": n, "invalid_record_count": 0,
        "first_event_timestamp": None, "last_event_timestamp": None, "ingestion_run_id": "replay",
        "commit_id": "replay-b00001", "ingested_at": datetime.datetime.now(datetime.timezone.utc),
        "status": "ingested", "message": None,
    }
    snapshot = frames.localCheckpoint()
    store.commit(spark, "replay-b00001", snapshot, None, [row])
    store.commit(spark, "replay-b00001", snapshot, None, [row])  # same commit id replayed
    assert store.read_frames(spark).count() == 2 * n  # appended once, not twice


def test_delta_bronze_matches_parquet_bronze(spark, tmp_path):
    from fleetpulse.pipelines.delta_parity import RUN_COLUMNS

    pq, _ = ingest(spark, tmp_path, fmt="parquet")
    dl, _ = ingest(spark, tmp_path, fmt="delta")
    cols = [c for c in BRONZE_CAN_FRAMES.fieldNames() if c not in RUN_COLUMNS]
    a, b = pq.read_frames(spark).select(*cols), dl.read_frames(spark).select(*cols)
    assert a.count() == b.count() == 5 and a.exceptAll(b).count() == 0 and b.exceptAll(a).count() == 0
    qa, qb = pq.read_quarantine(spark).drop(*RUN_COLUMNS), dl.read_quarantine(spark).drop(*RUN_COLUMNS)
    assert qa.exceptAll(qb).count() == 0 and qb.exceptAll(qa).count() == 0


# --- Silver on Delta, end to end with real files ---------------------------------
REAL = Path(__file__).resolve().parents[1] / "data/raw/part_3"
REAL_FILES = ["20210119101259207093.csv", "20210119101358162388.csv", "20210119105907167768.csv"]


@pytest.fixture(scope="module")
def lakes(spark, tmp_path_factory):
    if not all((REAL / f).exists() for f in REAL_FILES):
        pytest.skip("real part_3 files not extracted")
    from fleetpulse.pipelines.silver_build import build_silver

    tmp = tmp_path_factory.mktemp("delta_e2e")
    archive = tmp / "real.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        for f in REAL_FILES:
            tar.add(REAL / f, arcname=f"part_3/{f}")
    out = {}
    for fmt in ("parquet", "delta"):
        BronzeIngestion(spark, open_bronze_store(tmp / fmt / "bronze", fmt)).run(archive)
        out[fmt] = build_silver(spark, tmp / fmt / "bronze", tmp / fmt / "silver", storage_format=fmt)
    return tmp, out


def test_delta_matches_parquet_for_bronze_and_silver(spark, lakes):
    from fleetpulse.pipelines.delta_parity import run

    tmp, _ = lakes
    report = run(spark, tmp / "parquet/bronze", tmp / "delta/bronze", tmp / "parquet/silver", tmp / "delta/silver")
    assert report["parity"], report
    assert report["tables"]["silver_telemetry"]["delta_rows"] > 1000
    assert report["lineage"]["delta"]["events"] > 20000


def test_silver_delta_rebuild_is_deterministic(spark, lakes):
    from fleetpulse.pipelines.silver_build import build_silver
    from fleetpulse.silver.storage import TABLES

    tmp, out = lakes
    m2 = build_silver(spark, tmp / "delta/bronze", tmp / "delta/silver", storage_format="delta")
    v1, v2 = out["delta"]["delta_table_versions"], m2["delta_table_versions"]
    for t in TABLES:
        assert v2[t] == v1[t] + 1
        a = spark.read.format("delta").option("versionAsOf", v1[t]).load(str(tmp / "delta/silver" / t)).drop("silver_build_id")
        b = spark.read.format("delta").option("versionAsOf", v2[t]).load(str(tmp / "delta/silver" / t)).drop("silver_build_id")
        assert a.count() == b.count() and a.exceptAll(b).count() == 0 and b.exceptAll(a).count() == 0


def test_failed_silver_build_never_exposes_a_mixed_snapshot(spark, lakes, monkeypatch):
    from fleetpulse.pipelines import silver_build
    from fleetpulse.silver.storage import EVENTS, SESSIONS, TELEMETRY_TABLE
    from fleetpulse.silver.storage_delta import DeltaSilverStore

    tmp, _ = lakes
    root = tmp / "delta/silver"
    store = DeltaSilverStore(root)
    good = store.latest_manifest()["silver_build_id"]

    def boom(*a, **k):
        raise RuntimeError("simulated failure after events were written")

    monkeypatch.setattr(silver_build.alignment, "build", boom)
    with pytest.raises(RuntimeError, match="simulated failure"):
        silver_build.build_silver(spark, tmp / "delta/bronze", root, storage_format="delta")
    latest_events = spark.read.format("delta").load(str(root / EVENTS)).select("silver_build_id").distinct().collect()
    assert latest_events[0][0] != good  # the events table itself has a newer, orphaned version
    for t in (EVENTS, SESSIONS, TELEMETRY_TABLE):  # but readers see the last completed build everywhere
        ids = {r[0] for r in store.read(spark, t).select("silver_build_id").distinct().collect()}
        assert ids == {good}, t


def test_silver_delta_schema_enforced_on_overwrite(spark, lakes):
    from pyspark.errors import AnalysisException
    from pyspark.sql import functions as F

    from fleetpulse.silver.storage import SESSIONS
    from fleetpulse.silver.storage_delta import DeltaSilverStore

    tmp, _ = lakes
    path = str(tmp / "delta/silver" / SESSIONS)
    bad = DeltaSilverStore(tmp / "delta/silver").read(spark, SESSIONS).withColumn("trip_id", F.lit(1))
    with pytest.raises(AnalysisException):
        bad.write.format("delta").mode("overwrite").save(path)
