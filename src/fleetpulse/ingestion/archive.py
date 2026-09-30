"""Selecting and extracting a contiguous window of files from a raw archive.

Turku log files are named after the logger's start time, e.g.
``20210119101259207093.csv`` = 2021-01-19 10:12:59.207093. Filename order is
only used to *select candidates*; continuity must be verified from the
timestamps inside the files (see :mod:`fleetpulse.validation.continuity`).
"""

from __future__ import annotations

import logging
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

log = logging.getLogger(__name__)

FILENAME_TIME_FORMAT = "%Y%m%d%H%M%S%f"


@dataclass(frozen=True)
class ArchiveMember:
    name: str  # path inside the archive, e.g. part_3/2021....csv
    size: int

    @property
    def start_time(self) -> datetime:
        return filename_start_time(self.name)


def filename_start_time(name: str) -> datetime:
    stem = PurePosixPath(name).stem
    return datetime.strptime(stem[:20], FILENAME_TIME_FORMAT)


def list_csv_members(archive: Path) -> list[ArchiveMember]:
    """List CSV members sorted by filename start time (streams the archive)."""
    with tarfile.open(archive, "r:*") as tar:
        members = [ArchiveMember(m.name, m.size) for m in tar if m.isfile() and m.name.endswith(".csv")]
    return sorted(members, key=lambda m: m.start_time)


def select_window(members: list[ArchiveMember], anchor: str, before: int, after: int) -> list[ArchiveMember]:
    """``before`` files preceding and ``after`` files following ``anchor``."""
    names = [PurePosixPath(m.name).name for m in members]
    i = names.index(PurePosixPath(anchor).name)
    return members[max(0, i - before) : i + after + 1]


def extract_members(archive: Path, members: list[ArchiveMember], dest: Path) -> list[Path]:
    """Extract ``members`` under ``dest``; never overwrites an existing file."""
    wanted = {m.name for m in members}
    written: list[Path] = []
    with tarfile.open(archive, "r:*") as tar:
        for info in tar:
            if info.name not in wanted:
                continue
            target = dest / info.name
            if target.exists():
                log.info("Exists, not overwritten: %s", target)
            else:
                tar.extract(info, dest, filter="data")
                log.info("Extracted %s", target)
            written.append(target)
    missing = wanted - {str(p.relative_to(dest)) for p in written}
    if missing:
        raise FileNotFoundError(f"Not found in {archive}: {sorted(missing)}")
    return written
