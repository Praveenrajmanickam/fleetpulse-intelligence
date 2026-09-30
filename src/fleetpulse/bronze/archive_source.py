"""Reading source files out of a tar(.xz) archive, one member at a time.

A .tar.xz is a single compressed stream: it cannot be split or read in
parallel by Spark, and it has no random access. So archive reading is kept
out of Spark:

1. :func:`iter_sources` streams the archive once, in archive order, holding
   at most one member's bytes in memory, and computes its SHA-256.
2. :func:`stage` writes selected members as plain files into a temporary
   staging directory that Spark reads (``wholetext``) and the caller deletes
   after the batch is committed. Only one batch is ever on disk.

On Databricks this module is replaced by a landing zone (extracted files or
Auto Loader); :mod:`fleetpulse.bronze.parsing` is unchanged.
"""

from __future__ import annotations

import hashlib
import tarfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceFile:
    source_archive: str  # archive file name, e.g. part_3.tar.xz
    source_file: str  # member path inside the archive, e.g. part_3/2021....csv
    sha256: str
    size_bytes: int
    data: bytes


def read_member_list(path: Path) -> list[str]:
    """Member paths from a text file (one per line, '#' comments allowed)."""
    lines = [ln.strip() for ln in Path(path).read_text().splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")]


def iter_sources(archive: Path, include: Iterable[str] | None = None) -> Iterator[SourceFile]:
    """Yield CSV members of ``archive`` (optionally only ``include``), in archive order.

    Raises FileNotFoundError at the end if requested members were not found.
    """
    archive = Path(archive)
    wanted = set(include) if include is not None else None
    found = set()
    with tarfile.open(archive, "r:*") as tar:
        for info in tar:
            if not (info.isfile() and info.name.endswith(".csv")):
                continue
            if wanted is not None and info.name not in wanted:
                continue
            data = tar.extractfile(info).read()
            found.add(info.name)
            yield SourceFile(
                source_archive=archive.name,
                source_file=info.name,
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
                data=data,
            )
    if wanted is not None and wanted - found:
        raise FileNotFoundError(f"not in {archive.name}: {sorted(wanted - found)}")


def stage(source: SourceFile, staging_dir: Path, seq: int) -> str:
    """Write ``source`` into ``staging_dir``; returns the staged file name.

    Staged names are unique within a batch (``<seq>__<basename>``) so Spark's
    file path can be mapped back to the archive member.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    name = f"{seq:06d}__{Path(source.source_file).name}"
    (staging_dir / name).write_bytes(source.data)
    return name
