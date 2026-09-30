"""Compare Bronze output with the reference Python reader for the same source files.

Usage::

    python -m fleetpulse.pipelines.bronze_compare \\
        --archive data/raw/part_3.tar.xz \\
        --include-list config/bronze_subset_part3.txt --root data/bronze

For every member: the reference reader (fleetpulse.ingestion.can_csv +
validation.frames) runs on a temporary copy; Bronze rows are read with
pyarrow (no Spark/pandas interop). Checks:

* frame counts: reference rows == Bronze valid + quarantine; valid == valid
* per source_line: timestamp, CAN ID, DLC and all 8 byte values equal
* raw fidelity: every source line rebuilt from Bronze equals the original
  text (``str(timestamp);0x<id hex>;dlc;bytes...``)
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds

from fleetpulse.bronze.archive_source import iter_sources, read_member_list
from fleetpulse.bronze.schema import PAYLOAD_COLUMNS
from fleetpulse.ingestion.can_csv import read_can_csv
from fleetpulse.validation.frames import flag_invalid_rows


def _bronze(table_dir: Path, source_file: str) -> pd.DataFrame:
    if not table_dir.exists():
        return pd.DataFrame()
    dataset = ds.dataset(table_dir, format="parquet", partitioning="hive")
    return dataset.to_table(filter=pc.field("source_file") == source_file).to_pandas()


def rebuild_line(row) -> str:
    fields = [str(row.event_timestamp.to_pydatetime()), f"0x{int(row.can_id):x}", str(int(row.dlc))]
    fields += [str(int(getattr(row, c))) for c in PAYLOAD_COLUMNS[: int(row.dlc)]]
    return ";".join(fields)


def compare_member(src, root: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(src.source_file).name
        path.write_bytes(src.data)
        ref = flag_invalid_rows(read_can_csv(path))
    original = src.data.decode("ascii").split("\n")
    original = [ln.rstrip("\r") for ln in original]

    frames = _bronze(root / "bronze_can_frames", src.source_file).sort_values("source_line")
    quarantine = _bronze(root / "bronze_quarantine", src.source_file)
    ref_valid = ref[ref["is_valid"]].set_index("source_line")
    b = frames.set_index("source_line")

    out = {
        "source_file": src.source_file,
        "sha256": src.sha256,
        "reference_rows": int(len(ref)),
        "reference_valid": int(ref["is_valid"].sum()),
        "bronze_valid": int(len(frames)),
        "bronze_quarantine": int(len(quarantine)),
        "event_dates": sorted(str(d) for d in frames["event_date"].unique()) if len(frames) else [],
    }
    out["counts_match"] = (
        out["reference_rows"] == out["bronze_valid"] + out["bronze_quarantine"]
        and out["reference_valid"] == out["bronze_valid"]
    )
    same_index = b.index.equals(ref_valid.index)
    value_checks = {"source_line": bool(same_index)}
    if same_index:
        value_checks["timestamp"] = bool((b["event_timestamp"].values == ref_valid["timestamp"].values).all())
        value_checks["can_id"] = bool((b["can_id"].to_numpy(np.int64) == ref_valid["can_id"].to_numpy(np.int64)).all())
        value_checks["dlc"] = bool((b["dlc"].to_numpy(np.int64) == ref_valid["dlc"].to_numpy(np.int64)).all())
        for i, c in enumerate(PAYLOAD_COLUMNS):
            value_checks[c] = bool(b[c].astype("Int64").reset_index(drop=True).equals(
                ref_valid[f"b{i}"].astype("Int64").reset_index(drop=True).rename(c)))
    out["values_match"] = all(value_checks.values())
    out["value_checks"] = value_checks

    rebuilt = [rebuild_line(r) for r in frames.itertuples(index=False)]
    orig = [original[int(n) - 1] for n in frames["source_line"]]
    mismatches = [(int(n), a, o) for n, a, o in zip(frames["source_line"], rebuilt, orig) if a != o]
    out["raw_lines_identical"] = len(frames) - len(mismatches)
    out["raw_line_mismatches"] = mismatches[:5]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--include-list", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("data/bronze"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    results = [compare_member(s, args.root) for s in iter_sources(args.archive, read_member_list(args.include_list))]
    totals = {
        k: sum(r[k] for r in results)
        for k in ("reference_rows", "reference_valid", "bronze_valid", "bronze_quarantine", "raw_lines_identical")
    }
    report = {
        "files": len(results),
        "totals": totals,
        "all_counts_match": all(r["counts_match"] for r in results),
        "all_values_match": all(r["values_match"] for r in results),
        "all_raw_lines_identical": totals["raw_lines_identical"] == totals["bronze_valid"],
        "per_file": results,
    }
    out = args.out or args.root / "_run_reports" / "reference_comparison.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "per_file"}, indent=2))
    ok = report["all_counts_match"] and report["all_values_match"] and report["all_raw_lines_identical"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
