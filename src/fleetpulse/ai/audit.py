"""AI audit records. Storage is behind ``AuditStore`` so it can be replaced (DB, Delta, log sink).

Recorded: request id, timestamp, scope, question (secret-redacted), evidence ids, Gold build,
rule version, provider, model, status, validation outcome and (optionally) the final answer.
Never recorded: API keys, the system prompt / evidence envelope, secrets, reasoning."""

from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path

AUDIT_FIELDS = ["request_id", "timestamp_utc", "scope", "subject_id", "intent", "question", "input_flags",
                "evidence_ids", "gold_build_id", "rule_version", "provider", "model", "is_mock", "status",
                "answer_source", "validation_passed", "validation_issues", "attempts", "latency_ms", "answer",
                "policy_version", "prompt_version"]


class AuditStore(ABC):
    @abstractmethod
    def write(self, record: dict) -> None: ...

    @abstractmethod
    def recent(self, limit: int = 50) -> list[dict]: ...


class InMemoryAuditStore(AuditStore):
    def __init__(self):
        self.records: list[dict] = []

    def write(self, record: dict) -> None:
        self.records.append(dict(record))

    def recent(self, limit: int = 50) -> list[dict]:
        return self.records[-limit:][::-1]


class JsonlAuditStore(AuditStore):
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, default=str, ensure_ascii=False)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def recent(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(line) for line in reversed(lines) if line.strip()]


def audit_store_from_env(env: dict | None = None) -> AuditStore:
    env = os.environ if env is None else env
    return JsonlAuditStore(Path(env.get("FLEETPULSE_AI_AUDIT_PATH", "data/ai_audit/ai_audit.jsonl")))


def make_record(**kw) -> dict:
    unknown = set(kw) - set(AUDIT_FIELDS)
    if unknown:
        raise ValueError(f"unexpected audit fields {sorted(unknown)}")
    return {k: kw.get(k) for k in AUDIT_FIELDS}
