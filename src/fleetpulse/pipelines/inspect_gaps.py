"""Inspect the timestamp-gap distribution of raw CAN logs (no decoding).

Purpose: collect the evidence needed to justify the recording-session gap
threshold (``build_session --recording-gap-s``) from the full dataset.

Usage::

    # extracted files
    python -m fleetpulse.pipelines.inspect_gaps data/raw/part_3/*.csv --name part_3_sample

    # straight from the archive, streaming, nothing extracted to disk
    python -m fleetpulse.pipelines.inspect_gaps --archive data/raw/part_3.tar.xz --name part_3_full

Each file is summarised independently (the archive is read once, in archive
order); file boundaries are evaluated after ordering files by their first
timestamp. Writes the same report files as build_session's gap scan to
``<out>/gap_inspection/<name>/``: gap_report.json, gap_distribution.csv,
recording_sessions.csv, session_breaks.csv, largest_gaps.csv, files.csv.
The threshold only affects which gaps are listed as session breaks; the
distribution does not depend on it.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tarfile
import time
from pathlib import Path

from fleetpulse.ingestion.can_csv import read_can_timestamps
from fleetpulse.validation.continuity import DEFAULT_RECORDING_GAP_S, assemble, summarise_file, write_report

log = logging.getLogger("fleetpulse.inspect_gaps")


def _summaries_from_files(paths, recording_gap_s):
    for p in sorted(paths):
        t = read_can_timestamps(p)
        yield summarise_file(Path(p).name, t["timestamp"], t["source_line"], recording_gap_s)


def _summaries_from_archive(archive: Path, recording_gap_s):
    with tarfile.open(archive, "r:*") as tar:
        for info in tar:
            if not (info.isfile() and info.name.endswith(".csv")):
                continue
            t = read_can_timestamps(tar.extractfile(info), info.name)
            yield summarise_file(Path(info.name).name, t["timestamp"], t["source_line"], recording_gap_s)


def inspect(inputs: list[Path], archive: Path | None, out_dir: Path, recording_gap_s: float) -> dict:
    t0 = time.perf_counter()
    source = _summaries_from_archive(archive, recording_gap_s) if archive else _summaries_from_files(inputs, recording_gap_s)
    summaries = []
    for i, s in enumerate(source, 1):
        summaries.append(s)
        if i % 100 == 0:
            log.info("%d files scanned", i)
    result = assemble(summaries, recording_gap_s)
    paths = write_report(result, out_dir)
    log.info(
        "%d files, %d frames, %d gaps in %.1f s; max gap %.3f s; %d gap(s) > %.3f s -> %d recording session(s)",
        result.stats["files"], result.stats["frames"], result.stats["gaps_measured"], time.perf_counter() - t0,
        result.stats["max_gap_s"], len(result.breaks), recording_gap_s, len(result.sessions),
    )
    log.info("Report: %s", out_dir)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("inputs", nargs="*", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--name", required=True, help="report name")
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--recording-gap-s", type=float, default=DEFAULT_RECORDING_GAP_S)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    if not args.inputs and not args.archive:
        parser.error("give input files or --archive")
    inspect(args.inputs, args.archive, args.output_dir / "gap_inspection" / args.name, args.recording_gap_s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
