"""Bronze layer (PySpark): parsing, quarantine, lineage, partitioning, idempotency."""

import io
import shutil
import tarfile
from datetime import date, datetime
from pathlib import Path

import pytest

pyspark = pytest.importorskip("pyspark")

from fleetpulse.bronze.ingestion_log import SourceConflictError  # noqa: E402
from fleetpulse.bronze.schema import (  # noqa: E402
    BRONZE_CAN_FRAMES,
    BRONZE_QUARANTINE,
    INGESTION_LOG,
    PAYLOAD_COLUMNS,
)
from fleetpulse.bronze.storage import ParquetBronzeStore  # noqa: E402
from fleetpulse.pipelines.bronze_ingest import BronzeIngestion  # noqa: E402

HEADER = "timestamp;id;dlc;data"
L_DLC8 = "2021-01-19 10:12:59.220499;0xcf004e6;8;240;125;131;194;18;255;255;255"  # EEC1 600.25 rpm
L_DLC4 = "2021-01-19 10:12:59.278855;0x1cff9053;4;254;31;59;128"
L_DLC1 = "2021-01-19 10:12:59.279253;0x1cff80e6;1;230"
L_DLC0 = "2021-01-19 10:12:59.300000;0x18eaff00;0"
L_WHOLE_SECOND = "2021-01-19 10:16:51;0x10ff80e6;8;0;0;252;109;240;144;255;255"


@pytest.fixture(scope="module")
def spark():
    from fleetpulse.bronze.spark import get_spark

    s = get_spark(app="bronze-tests", master="local[2]", driver_memory="1g", shuffle_partitions=2)
    yield s
    s.stop()


def make_archive(path: Path, members: dict[str, str], crlf: bool = False) -> Path:
    """Write a tar.xz with members {name: text}; text lines joined by \\n (or CRLF)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:xz") as tar:
        for name, text in members.items():
            data = text.replace("\n", "\r\n").encode() if crlf else text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def csv(*lines: str) -> str:
    return "\n".join([HEADER, *lines]) + "\n"


def ingest(spark, tmp_path, members, root=None, archive_name="part_x.tar.xz", **kw):
    archive = make_archive(tmp_path / archive_name, members, crlf=kw.pop("crlf", False))
    store = ParquetBronzeStore(root or tmp_path / "bronze")
    summary = BronzeIngestion(spark, store, **kw).run(archive)
    return store, summary


def frames(spark, store):
    return {(r.source_file, r.source_line): r for r in store.read_frames(spark).collect()}


def quarantine(spark, store):
    if not store.quarantine_dir.exists():
        return {}
    return {(r.source_file, r.source_line): r for r in store.read_quarantine(spark).collect()}


# 1 ---------------------------------------------------------------------------
def test_normal_dlc8_frame(spark, tmp_path):
    store, s = ingest(spark, tmp_path, {"p/a.csv": csv(L_DLC8)})
    (r,) = frames(spark, store).values()
    assert r.event_timestamp == datetime(2021, 1, 19, 10, 12, 59, 220499)
    assert r.can_id == 0x0CF004E6 and r.dlc == 8
    assert [r[c] for c in PAYLOAD_COLUMNS] == [240, 125, 131, 194, 18, 255, 255, 255]
    assert (r.source_archive, r.source_file, r.source_line) == ("part_x.tar.xz", "p/a.csv", 2)
    assert r.ingestion_run_id == s["ingestion_run_id"]


# 2 ---------------------------------------------------------------------------
def test_short_dlc_frames_keep_missing_bytes_null(spark, tmp_path):
    store, _ = ingest(spark, tmp_path, {"p/a.csv": csv(L_DLC4, L_DLC1, L_DLC0)})
    rows = frames(spark, store)
    b = lambda line: [rows[("p/a.csv", line)][c] for c in PAYLOAD_COLUMNS]  # noqa: E731
    assert b(2) == [254, 31, 59, 128, None, None, None, None]
    assert b(3) == [230] + [None] * 7
    assert b(4) == [None] * 8 and rows[("p/a.csv", 4)].dlc == 0


# 3-6 -------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line,reason",
    [
        ("2021-01-19T10:12:59.220499;0xcf004e6;1;1", "bad_timestamp"),
        ("2021-02-30 10:12:59.2;0xcf004e6;1;1", "bad_timestamp"),
        ("2021-01-19 10:12:59.2204991;0xcf004e6;1;1", "bad_timestamp"),
        ("not a time;0xcf004e6;1;1", "bad_timestamp"),
        ("2021-01-19 10:12:59.1;0xZZ;1;1", "bad_can_id"),
        ("2021-01-19 10:12:59.1;;1;1", "bad_can_id"),
        ("2021-01-19 10:12:59.1;0x123456789;1;1", "bad_can_id"),
        ("2021-01-19 10:12:59.1;0x2fffffff;1;1", "can_id_exceeds_29_bits"),
        ("2021-01-19 10:12:59.1;0xcf004e6;1;256", "payload_byte_out_of_range"),
        ("2021-01-19 10:12:59.1;0xcf004e6;1;ab", "bad_payload_byte"),
        ("2021-01-19 10:12:59.1;0xcf004e6;2;1;", "bad_payload_byte"),
        ("2021-01-19 10:12:59.1;0xcf004e6;8;1;2;3", "dlc_payload_mismatch"),
        ("2021-01-19 10:12:59.1;0xcf004e6;2;1;2;3", "dlc_payload_mismatch"),
        ("2021-01-19 10:12:59.1;0xcf004e6;9;1;2;3;4;5;6;7;8;9", "bad_dlc"),
        ("2021-01-19 10:12:59.1;0xcf004e6;x;1", "bad_dlc"),
        ("2021-01-19 10:12:59.1;0xcf004e6;8;1;2;3;4;5;6;7;8;9", "too_many_payload_fields"),
        ("2021-01-19 10:12:59.1;0xcf004e6", "too_few_fields"),
        ("", "empty_line"),
    ],
)
def test_malformed_records_are_quarantined(spark, tmp_path, line, reason):
    store, s = ingest(spark, tmp_path, {"p/a.csv": csv(L_DLC8, line, L_DLC1)})
    q = quarantine(spark, store)
    assert list(q) == [("p/a.csv", 3)]
    r = q[("p/a.csv", 3)]
    assert r.failure_reason == reason and reason in r.failure_reasons
    assert r.raw_record == line
    assert set(frames(spark, store)) == {("p/a.csv", 2), ("p/a.csv", 4)}
    assert (s["valid_records"], s["invalid_records"]) == (2, 1)


def test_header_mismatch_quarantines_every_record(spark, tmp_path):
    store, s = ingest(spark, tmp_path, {"p/a.csv": "ts,id,dlc\n" + L_DLC8 + "\n" + L_DLC1 + "\n"})
    assert not store.frames_dir.exists()
    q = quarantine(spark, store)
    assert {r.failure_reason for r in q.values()} == {"file_header_mismatch"} and len(q) == 2
    (row,) = store.read_log(spark).collect()
    assert row.header_ok is False and row.invalid_record_count == 2


# 7 ---------------------------------------------------------------------------
def test_source_line_lineage_with_crlf_and_quarantine_in_between(spark, tmp_path):
    lines = [L_DLC8, "garbage", L_DLC4, "", L_WHOLE_SECOND]
    store, _ = ingest(spark, tmp_path, {"p/a.csv": csv(*lines)}, crlf=True)
    f, q = frames(spark, store), quarantine(spark, store)
    assert sorted(k[1] for k in f) == [2, 4, 6]
    assert sorted(k[1] for k in q) == [3, 5]
    assert f[("p/a.csv", 4)].can_id == 0x1CFF9053
    assert q[("p/a.csv", 3)].raw_record == "garbage"  # CR stripped, content intact


# 8 ---------------------------------------------------------------------------
def test_rerun_same_source_does_not_duplicate(spark, tmp_path):
    members = {"p/a.csv": csv(L_DLC8, L_DLC4), "p/b.csv": csv(L_DLC1)}
    store, s1 = ingest(spark, tmp_path, members)
    store, s2 = ingest(spark, tmp_path, members)
    assert s1["sources"]["ingested"] == 2 and s2["sources"]["ingested"] == 0
    assert s2["sources"]["skipped_already_ingested"] == 2 and s2["records"] == 0
    assert store.read_frames(spark).count() == 3
    statuses = sorted(r.status for r in store.read_log(spark).collect())
    assert statuses == ["ingested", "ingested", "skipped_already_ingested", "skipped_already_ingested"]


# 9 ---------------------------------------------------------------------------
def test_same_filename_changed_checksum_is_a_conflict(spark, tmp_path):
    root = tmp_path / "bronze"
    ingest(spark, tmp_path / "v1", {"p/a.csv": csv(L_DLC8), "p/b.csv": csv(L_DLC1)}, root=root)
    changed = {"p/a.csv": csv(L_DLC8, L_DLC4), "p/b.csv": csv(L_DLC1), "p/c.csv": csv(L_DLC0)}
    with pytest.raises(SourceConflictError) as exc:
        ingest(spark, tmp_path / "v2", changed, root=root)
    assert exc.value.conflicts[0]["source_file"] == "p/a.csv"
    store = ParquetBronzeStore(root)
    log_rows = store.read_log(spark).collect()
    assert [r.status for r in log_rows if r.status == "conflict_checksum_changed"] == ["conflict_checksum_changed"]
    assert store.read_frames(spark).count() == 2  # nothing from the changed file

    # With on_conflict="skip" the conflict is logged again and other new files proceed.
    _, s = ingest(spark, tmp_path / "v3", changed, root=root, on_conflict="skip")
    assert s["sources"] == {"ingested": 1, "skipped_already_ingested": 1, "conflict_checksum_changed": 1}
    assert {r.source_file for r in store.read_frames(spark).collect()} == {"p/a.csv", "p/b.csv", "p/c.csv"}
    assert store.read_frames(spark).where("source_file = 'p/a.csv'").count() == 1  # still v1 only


# 10 --------------------------------------------------------------------------
def test_valid_and_quarantine_reconcile_with_source_records(spark, tmp_path):
    a = csv(L_DLC8, "bad", L_DLC4, "2021-01-19 10:00:00.1;0x1;1;999")
    b = csv(L_DLC1, L_DLC0)
    store, s = ingest(spark, tmp_path, {"p/a.csv": a, "p/b.csv": b})
    log_rows = {r.source_file: r for r in store.read_log(spark).collect()}
    for name, text in {"p/a.csv": a, "p/b.csv": b}.items():
        r = log_rows[name]
        n_lines = len(text.rstrip("\n").split("\n")) - 1
        assert r.record_count == n_lines == r.valid_record_count + r.invalid_record_count
        assert store.read_frames(spark).where(f"source_file = '{name}'").count() == r.valid_record_count
    assert store.read_quarantine(spark).count() == 2 == s["invalid_records"]


# 11 --------------------------------------------------------------------------
def test_event_date_derivation_and_partitioning(spark, tmp_path):
    lines = [
        "2021-02-10 23:59:59.999999;0x1;1;1",
        "2021-02-11 00:00:00;0x1;1;2",
        "2021-02-11 00:00:00.000001;0x1;1;3",
    ]
    store, _ = ingest(spark, tmp_path, {"p/a.csv": csv(*lines)})
    rows = frames(spark, store)
    assert rows[("p/a.csv", 2)].event_date == date(2021, 2, 10)
    assert rows[("p/a.csv", 3)].event_date == date(2021, 2, 11)
    assert rows[("p/a.csv", 4)].event_date == date(2021, 2, 11)
    parts = sorted(p.name for p in store.frames_dir.iterdir())
    assert parts == ["event_date=2021-02-10", "event_date=2021-02-11"]
    assert all(len(list(p.glob("*.parquet"))) == 1 for p in store.frames_dir.iterdir())


# 12 --------------------------------------------------------------------------
def test_multiple_files_across_batches(spark, tmp_path):
    members = {f"p/{i}.csv": csv(L_DLC8, L_DLC1) for i in range(5)}
    store, s = ingest(spark, tmp_path, members, batch_files=2)
    assert len(s["commits"]) == 3
    rows = frames(spark, store)
    assert len(rows) == 10
    assert {k[0] for k in rows} == set(members)
    assert len({r.ingestion_run_id for r in rows.values()}) == 1
    # one file per event_date per commit, not one per input CSV
    assert len(list(store.frames_dir.rglob("*.parquet"))) == 3


# 13 --------------------------------------------------------------------------
def test_raw_payload_survives_bronze_unchanged(spark, tmp_path):
    lines = [L_DLC8, L_DLC4, L_DLC1, L_DLC0, L_WHOLE_SECOND]
    store, _ = ingest(spark, tmp_path, {"p/a.csv": csv(*lines)})
    rows = sorted(frames(spark, store).values(), key=lambda r: r.source_line)
    rebuilt = [
        ";".join([str(r.event_timestamp), f"0x{r.can_id:x}", str(r.dlc)] + [str(r[c]) for c in PAYLOAD_COLUMNS[: r.dlc]])
        for r in rows
    ]
    assert rebuilt == lines


# storage ---------------------------------------------------------------------
def test_schemas_are_explicit_and_written_as_declared(spark, tmp_path):
    store, _ = ingest(spark, tmp_path, {"p/a.csv": csv(L_DLC8, "bad")})
    import pyarrow.parquet as pq

    (f,) = store.frames_dir.rglob("*.parquet")
    arrow = pq.read_schema(f)
    assert str(arrow.field("event_timestamp").type) == "timestamp[us]"  # no timezone
    assert str(arrow.field("ingested_at").type) == "timestamp[us, tz=UTC]"
    assert str(arrow.field("byte_1").type) == "int16" and str(arrow.field("can_id").type) == "int32"
    for table, schema in [(store.read_frames(spark), BRONZE_CAN_FRAMES),
                          (store.read_quarantine(spark), BRONZE_QUARANTINE),
                          (store.read_log(spark), INGESTION_LOG)]:
        assert [f.name for f in table.schema] == [f.name for f in schema]


def test_recover_removes_uncommitted_files(spark, tmp_path):
    store, _ = ingest(spark, tmp_path, {"p/a.csv": csv(L_DLC8)})
    (good,) = store.frames_dir.rglob("*.parquet")
    orphan = good.parent / "run-x-b00001__part-orphan.parquet"
    shutil.copy(good, orphan)
    (store.staging_root / "run-x-b00001").mkdir(parents=True)
    assert store.recover() == {"staging_dirs": 1, "orphan_files": 1}
    assert not orphan.exists() and good.exists()
    assert store.read_frames(spark).count() == 1


# reference comparison on real files -------------------------------------------
REAL = Path(__file__).resolve().parents[1] / "data/raw/part_3"
REAL_FILES = ["20210119101259207093.csv", "20210119101555909929.csv"]


@pytest.mark.skipif(not all((REAL / f).exists() for f in REAL_FILES), reason="real part_3 files not extracted")
def test_matches_reference_reader_on_real_files(spark, tmp_path):
    import pandas as pd

    from fleetpulse.ingestion.can_csv import read_can_csv
    from fleetpulse.validation.frames import flag_invalid_rows

    archive = tmp_path / "real.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        for f in REAL_FILES:
            tar.add(REAL / f, arcname=f"part_3/{f}")
    store = ParquetBronzeStore(tmp_path / "bronze")
    BronzeIngestion(spark, store).run(archive)
    bronze = store.read_frames(spark)
    for f in REAL_FILES:
        ref = flag_invalid_rows(read_can_csv(REAL / f))
        ref = ref[ref["is_valid"]].set_index("source_line")
        rows = bronze.where(f"source_file = 'part_3/{f}'").collect()
        assert len(rows) == len(ref)
        got = pd.DataFrame([r.asDict() for r in rows]).set_index("source_line").sort_index()
        assert (got.index == ref.index).all()
        assert (pd.to_datetime(got["event_timestamp"]).values == ref["timestamp"].values).all()
        assert (got["can_id"].values == ref["can_id"].astype("int64").values).all()
        assert (got["dlc"].values == ref["dlc"].astype("int64").values).all()
        for i, c in enumerate(PAYLOAD_COLUMNS):
            a = got[c].astype("Int64")
            b = ref[f"b{i}"].astype("Int64")
            assert a.equals(b.rename(c)), c
