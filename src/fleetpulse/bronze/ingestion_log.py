"""Idempotency decisions from the committed Bronze ingestion log. Pure Python.

A source is identified by its member path inside the archive
(``source_file``, e.g. ``part_3/20210119101259207093.csv``) and versioned by
its SHA-256:

* never ingested                         -> INGEST
* ingested before with the same SHA-256  -> SKIP (no duplicate rows)
* ingested before with another SHA-256   -> CONFLICT (never silently skipped
  or re-ingested; surfaced to the caller)

The archive name is recorded but is not part of the identity, so re-packing
the same files into a new archive does not duplicate them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum


class Decision(str, Enum):
    INGEST = "ingest"
    SKIP = "skip"
    CONFLICT = "conflict"


class SourceConflictError(RuntimeError):
    """A source file was seen again with different content."""

    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        lines = [
            f"{c['source_file']}: new sha256 {c['new_sha256'][:12]}.. != ingested {sorted(c['ingested_sha256'])}"
            for c in conflicts
        ]
        super().__init__("source checksum conflict(s):\n  " + "\n  ".join(lines))


@dataclass
class IngestionLedger:
    """What has been committed (from the log) plus what this run has decided."""

    ingested: dict[str, set[str]] = field(default_factory=dict)  # source_file -> sha256s

    @classmethod
    def from_log_rows(cls, rows: Iterable[dict]) -> IngestionLedger:
        ledger = cls()
        for r in rows:
            if r["status"] == "ingested":
                ledger.ingested.setdefault(r["source_file"], set()).add(r["source_sha256"])
        return ledger

    def decide(self, source_file: str, sha256: str) -> Decision:
        known = self.ingested.get(source_file)
        if not known:
            return Decision.INGEST
        return Decision.SKIP if sha256 in known else Decision.CONFLICT

    def record_ingested(self, source_file: str, sha256: str) -> None:
        """Called as soon as a source is accepted into a batch, so a second copy
        of the same member later in the same run is also skipped / flagged."""
        self.ingested.setdefault(source_file, set()).add(sha256)
