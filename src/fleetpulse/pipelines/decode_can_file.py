"""Per-file pipeline: raw CAN CSV -> parsed J1939 frames + verified signals.

Usage::

    python -m fleetpulse.pipelines.decode_can_file \\
        data/raw/part_3/20210119101259207093.csv

Outputs (``<rel>`` mirrors the file's path under ``--raw-root``)::

    <out>/can_frames/<rel>.parquet                  every frame, J1939 ID fields, raw bytes
    <out>/signals/<msg>/<rel>.parquet               verified signals, one table per
                                                    message in j1939.definitions.MESSAGES
    <out>/reports/<rel>_pgn_summary.csv             per-PGN frequency table
    <out>/reports/<rel>_source_addresses.csv        per-source-address table
    <out>/reports/<rel>_report.json                 run metadata, quality, stats

The raw file is only read.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from fleetpulse import __version__
from fleetpulse.analysis.pgn_stats import (
    describe_signal,
    pgn_frequency_table,
    source_address_table,
)
from fleetpulse.ingestion.can_csv import BYTE_COLUMNS, file_sha256, read_can_csv
from fleetpulse.j1939.identifier import parse_can_ids
from fleetpulse.j1939.pgn_catalog import PgnCategory, classify_pgn, pgn_name
from fleetpulse.j1939.definitions import MESSAGES, MessageSpec
from fleetpulse.j1939.signals import decode_signal
from fleetpulse.validation.frames import build_quality_report, flag_invalid_rows

log = logging.getLogger("fleetpulse.pipeline")

ID_FIELD_DTYPES = {
    "priority": "UInt8",
    "extended_data_page": "UInt8",
    "data_page": "UInt8",
    "pdu_format": "UInt8",
    "pdu_specific": "UInt8",
    "source_address": "UInt8",
    "pgn": "UInt32",
    "is_pdu2": "boolean",
    "destination_address": "UInt8",
}


def build_frames(raw: pd.DataFrame) -> pd.DataFrame:
    """Validate raw rows and attach J1939 identifier fields and PGN labels.

    Invalid rows are kept (flagged) with <NA> identifier fields.
    """
    df = flag_invalid_rows(raw)
    valid = df["is_valid"].to_numpy()

    ids = df["can_id"].where(df["is_valid"], 0).astype("int64")
    fields = parse_can_ids(ids).astype(ID_FIELD_DTYPES)
    fields.loc[~valid, :] = pd.NA
    df = pd.concat([df, fields], axis=1)

    pgn_lookup = {int(p): (pgn_name(int(p)), classify_pgn(int(p)).value) for p in fields["pgn"].dropna().unique()}
    df["pgn_name"] = df["pgn"].map(lambda p: pgn_lookup[int(p)][0] if pd.notna(p) else pd.NA).astype("string")
    df["pgn_category"] = df["pgn"].map(lambda p: pgn_lookup[int(p)][1] if pd.notna(p) else pd.NA).astype("string")

    in_range = (df[BYTE_COLUMNS].isna() | df[BYTE_COLUMNS].apply(lambda c: c.between(0, 255))).all().all()
    if in_range:
        df[BYTE_COLUMNS] = df[BYTE_COLUMNS].astype("UInt8")
    else:
        log.warning("Out-of-range byte values present; keeping byte columns as Int16")
    return df


FRAME_CONTEXT_COLUMNS = ["timestamp", "source_line", "can_id", "priority", "source_address", "pgn"]


def decode_message(frames: pd.DataFrame, message: MessageSpec) -> tuple[pd.DataFrame, int]:
    """Decode every verified signal of ``message`` from valid 8-byte frames.

    Returns (decoded, n_frames_excluded_for_unexpected_dlc).
    """
    is_msg = frames["is_valid"] & (frames["pgn"] == message.pgn)
    bad_dlc = int((is_msg & (frames["dlc"] != 8)).sum())
    if bad_dlc:
        log.warning("%d %s frames with DLC != 8 excluded from decoding", bad_dlc, message.acronym)
    selected = frames.loc[is_msg & (frames["dlc"] == 8)]
    parts = [selected[FRAME_CONTEXT_COLUMNS]]
    parts += [decode_signal(selected, spec) for spec in message.signals]
    return pd.concat(parts, axis=1).reset_index(drop=True), bad_dlc


def run(input_path: Path, output_dir: Path, raw_root: Path) -> dict:
    input_path = input_path.resolve()
    try:
        rel = input_path.relative_to(raw_root.resolve()).with_suffix("")
    except ValueError:
        rel = Path(input_path.stem)
    log.info("Processing %s", input_path)

    raw = read_can_csv(input_path)
    frames = build_frames(raw)
    quality = build_quality_report(frames)
    valid = frames.loc[frames["is_valid"]]
    log.info("%d/%d rows valid", quality.valid_rows, quality.total_rows)

    pgn_table = pgn_frequency_table(valid, quality.duration_seconds)
    sa_table = source_address_table(valid)

    decoded: dict[str, pd.DataFrame] = {}
    signal_stats: dict[str, dict] = {}
    for message in MESSAGES:
        table, bad_dlc = decode_message(frames, message)
        decoded[message.key] = table
        log.info("%s frames decoded: %d", message.acronym, len(table))
        for spec in message.signals:
            signal_stats[spec.name] = {
                "pgn": spec.pgn,
                "spn": spec.spn,
                "reference": spec.reference,
                "source_addresses": sorted(int(a) for a in table["source_address"].unique()),
                "frames_excluded_bad_dlc": bad_dlc,
                **describe_signal(table[spec.name], table[f"{spec.name}_status"]),
            }

    frames = frames.assign(source_file=str(rel)).drop(columns=["n_bytes"])
    paths = {
        "frames": output_dir / "can_frames" / f"{rel}.parquet",
        **{f"signals_{k}": output_dir / "signals" / k / f"{rel}.parquet" for k in decoded},
        "pgn_summary": output_dir / "reports" / f"{rel}_pgn_summary.csv",
        "source_addresses": output_dir / "reports" / f"{rel}_source_addresses.csv",
        "report": output_dir / "reports" / f"{rel}_report.json",
    }
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)

    frames.to_parquet(paths["frames"], index=False)
    for key, table in decoded.items():
        table.to_parquet(paths[f"signals_{key}"], index=False)
    pgn_table.to_csv(paths["pgn_summary"], index=False, float_format="%.4f")
    sa_table.to_csv(paths["source_addresses"], index=False)

    unknown = pgn_table.loc[
        pgn_table["pgn_category"] != PgnCategory.STANDARD_DOCUMENTED.value,
        ["pgn", "pgn_hex", "pgn_category", "frame_count", "source_addresses"],
    ]
    report = {
        "run": {
            "pipeline": "decode_can_file",
            "version": __version__,
            "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "input_file": str(input_path),
            "input_sha256": file_sha256(input_path),
            "input_bytes": input_path.stat().st_size,
            "timestamp_timezone": "unknown (naive timestamps preserved as logged)",
        },
        "quality": quality.to_dict(),
        "totals": {
            "can_frames": quality.total_rows,
            "valid_frames": quality.valid_rows,
            "unique_can_ids": int(valid["can_id"].nunique()),
            "unique_pgns": int(valid["pgn"].nunique()),
            "unique_source_addresses": int(valid["source_address"].nunique()),
            "frames_by_pgn_category": {
                str(k): int(v) for k, v in valid["pgn_category"].value_counts().items()
            },
        },
        "top_pgns": pgn_table.head(20)[
            ["pgn", "pgn_hex", "pgn_name", "pgn_category", "frame_count", "share_pct", "source_addresses"]
        ].to_dict(orient="records"),
        "source_addresses": sa_table[["source_address", "source_address_hex", "frame_count", "unique_pgns"]].to_dict(
            orient="records"
        ),
        "unknown_pgns": unknown.to_dict(orient="records"),
        "signals": signal_stats,
        "outputs": {k: str(v) for k, v in paths.items()},
    }
    paths["report"].write_text(json.dumps(report, indent=2, default=_json_default))
    for name, p in paths.items():
        log.debug("Wrote %s -> %s", name, p)
    log.info("Wrote %d outputs for %s", len(paths), rel)
    return report


def _json_default(obj):
    if hasattr(obj, "item"):
        return obj.item()
    return str(obj)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("inputs", nargs="+", type=Path, help="raw CAN CSV file(s)")
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    for path in args.inputs:
        run(path, args.output_dir, args.raw_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
