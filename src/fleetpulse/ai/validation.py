"""Answer validation against the supplied evidence.

* numbers: every numeric token in the answer must match a value in the evidence package
  (at the precision the answer states it; duration/hour renderings are accepted);
* timestamps: dates/times must occur in the evidence;
* claims: prohibited claims asserted as fact (policy.scan_claims);
* leakage: system-prompt canary/fragments, API-key-like strings, the configured key.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from fleetpulse.ai.evidence import EvidencePackage
from fleetpulse.ai.policy import scan_claims
from fleetpulse.ai.prompt import CANARY

MAX_ANSWER_CHARS = 3000
# structural constants by unit class: one vehicle / Part 3; 1/5/15-minute classes, 0.1 h scope, 2 s minimum
CONSTANTS = {"count": {0, 1, 3}, "time": {1, 5, 15, 0.1, 2}, "pct": {100}}

_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?")
_TIME = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?![\d:])")
_IDS = re.compile(r"\b[\w-]*\d{8}T\d{6}[\w-]*\b|\bturku-[\w-]+|\bpart_\d+/\S+|\b[a-z0-9]+(?:-[a-z0-9]+){2,}\b", re.IGNORECASE)
_ENUM = re.compile(r"\(\d{1,2}\)|^\s*\d{1,2}[.)]\s", re.MULTILINE)
_NUM = re.compile(r"(?<![A-Za-z0-9_.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
# unit right after a number -> unit class (checked against evidence values of the same class)
_UNIT = re.compile(
    r"\s*(?P<u>km/h|kph|km|kilomet(?:re|er)s?|l/h|lph|litres?|liters?|l(?![a-z])|rpm|°\s*c|degrees?|%|percent|per ?cent|"
    r"h(?![a-z])|hours?|hrs?|min(?:ute)?s?(?![a-z])|m(?![a-z])|s(?![a-z])|sec(?:ond)?s?|eur|euros?|usd|gbp|"
    r"events?|sessions?)", re.IGNORECASE)
_UNIT_CLASS = [("kph", r"km/h|kph"), ("km", r"km|kilomet"), ("lph", r"l/h|lph"), ("l", r"lit|l$"), ("rpm", r"rpm"),
               ("c", r"°|degree"), ("pct", r"%|per ?cent"), ("time", r"^(h|hours?|hrs?|min|minutes?|m|s|sec|seconds?)$"),
               ("money", r"eur|usd|gbp"), ("count", r"event|session")]
_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}|\bsk-ant-[A-Za-z0-9_-]+|\bBearer\s+[A-Za-z0-9._-]{12,}")
_PROMPT_FRAGMENTS = ["You are the FleetPulse AI Analyst", "Reply with a single JSON object", "untrusted data from a user"]
_DURATION_KEY = re.compile(r"(duration|_s$|seconds|longest)", re.IGNORECASE)
_HOURS_KEY = re.compile(r"hours$", re.IGNORECASE)
_FACT_UNIT = {"s": "time", "h": "time", "L": "l", "L/h": "lph", "rpm": "rpm", "%": "pct", "°C": "c", "km": "km"}


@dataclass
class ValidationResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    unsupported_timestamps: list[str] = field(default_factory=list)
    claim_violations: list[dict] = field(default_factory=list)

    def corrective_note(self) -> str:
        return "; ".join(self.issues)[:600]


def _norm_ts(s: str) -> str:
    return s.replace("T", " ")


def _strip(text: str) -> str:
    text = _IDS.sub(" ", text)
    text = _DATETIME.sub(" ", text)
    text = _TIME.sub(" ", text)
    return _ENUM.sub(" ", text)


def _unit_class(unit: str | None) -> str | None:
    if not unit:
        return None
    u = unit.lower().replace(" ", "")
    for cls, pat in _UNIT_CLASS:
        if re.search(pat, u):
            return cls
    return None


def tokens_in(text: str) -> list[tuple[str, str | None]]:
    """(number, unit class or None) for each numeric token after stripping ids/timestamps/enumerations."""
    t = _strip(text)
    out = []
    for m in _NUM.finditer(t):
        um = _UNIT.match(t, m.end())
        out.append((m.group(1), _unit_class(um.group("u")) if um else None))
    return out


def numbers_in(text: str) -> list[str]:
    return [n for n, _ in tokens_in(text)]


def key_class(key: str) -> str:
    k = key.lower()
    for cls, pat in [("pct", r"pct|share|percent"), ("lph", r"lph|rate"), ("rpm", r"rpm"), ("kph", r"kph"),
                     ("km", r"km|distance"), ("c", r"_c$|coolant|temp"), ("money", r"cost|price|exposure|currency"),
                     ("time", r"duration|_s$|seconds|longest|hours|_min$|minutes|period_days|projection_days"),
                     ("l", r"_l$|fuel|litre")]:
        if re.search(pat, k):
            return cls
    return "count"


def _add_duration(out: set[float], v: float) -> None:
    out.update({v, v / 60, v / 3600})
    t = int(round(v))
    out.update({t // 60, t % 60, t // 3600, (t % 3600) // 60, round(v % 60, 1), math.floor(v / 60)})


def _add_hours(out: set[float], v: float) -> None:
    out.update({v, v * 60, v * 3600})
    m = int(round(v * 60))
    out.update({m // 60, m % 60})


def allowed_numbers(p: EvidencePackage) -> dict[str, set[float]]:
    """Evidence values by unit class. Numbers inside FleetPulse-generated text are classed by their unit."""
    out: dict[str, set[float]] = {k: set(v) for k, v in CONSTANTS.items()}

    def add(cls: str, v: float, key: str = "") -> None:
        bucket = out.setdefault(cls, set())
        if cls == "time" and _HOURS_KEY.search(key):
            _add_hours(bucket, v)
        elif cls == "time" and _DURATION_KEY.search(key):
            _add_duration(bucket, v)
        else:
            bucket.add(v)

    def walk(obj, key: str = "", cls: str | None = None):
        if isinstance(obj, bool) or obj is None:
            return
        if isinstance(obj, (int, float)):
            v = float(obj)
            if not math.isnan(v):
                add(cls or key_class(key), v, key)
        elif isinstance(obj, str):
            if key.endswith("_id") or key.endswith("_file"):
                return
            for n, ucls in tokens_in(obj):
                add(ucls or "count", float(n.replace(",", "")), "_s" if ucls == "time" else "")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, str(k), cls)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v, key, cls)

    walk(p.context)
    for f in p.facts:
        fcls = _FACT_UNIT.get(f.unit or "") or ("money" if f.source == "gold_scenario_engine" and f.unit and "/" not in f.unit
                                                  and f.unit.isalpha() else None)
        walk(f.value, f.id + ("_hours" if f.unit == "h" else "_s" if f.unit == "s" else ""), fcls)
        walk(f.display)
    for s_ in (*p.interpretation, *p.limitations, *p.investigations):
        walk(s_)
    if p.scenario:
        walk(p.scenario)
    return out


def evidence_timestamps(p: EvidencePackage) -> list[str]:
    found: list[str] = []

    def walk(obj):
        if isinstance(obj, str):
            found.extend(_norm_ts(m.group(0)) for m in _DATETIME.finditer(obj))
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v)

    walk(p.context)
    for f in p.facts:
        walk(f.value)
    return found


def _supported(token: str, unit_cls: str | None, allowed: dict[str, set[float]]) -> bool:
    """A unit-qualified number must match a value of the same unit class; a bare number may match any value."""
    x = float(token.replace(",", ""))
    decimals = len(token.split(".")[1]) if "." in token else 0
    tol = 0.5 * 10 ** -decimals + 1e-9
    pool = allowed.get(unit_cls, set()) if unit_cls else set().union(*allowed.values())
    return any(abs(x - v) <= tol + 1e-9 * max(1.0, abs(v)) for v in pool)


def extract_answer(raw: str) -> str | None:
    """The provider is asked for {"answer": "..."}; accept a JSON object (possibly fenced) or plain text."""
    text = (raw or "").strip()
    if not text:
        return None
    candidates = [text]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            obj = json.loads(c)
        except ValueError:
            continue
        if isinstance(obj, dict):
            ans = obj.get("answer")
            return ans.strip() if isinstance(ans, str) and ans.strip() else None
    if text.startswith("{"):
        return None  # malformed JSON: do not show it
    return text


def validate_answer(answer: str | None, p: EvidencePackage, secrets: tuple[str, ...] = ()) -> ValidationResult:
    r = ValidationResult(passed=True)
    if not answer:
        r.issues.append("empty or malformed answer")
    else:
        if len(answer) > MAX_ANSWER_CHARS:
            r.issues.append(f"answer longer than {MAX_ANSWER_CHARS} characters")
        allowed = allowed_numbers(p)
        r.unsupported_numbers = sorted({f"{t} ({u})" if u else t for t, u in tokens_in(answer) if not _supported(t, u, allowed)})
        if r.unsupported_numbers:
            r.issues.append(f"numbers not in the evidence: {', '.join(r.unsupported_numbers[:10])}")
        stamps = evidence_timestamps(p)
        bad_ts = [m.group(0) for m in _DATETIME.finditer(answer) if not any(s.startswith(_norm_ts(m.group(0))) for s in stamps)]
        bad_ts += [m.group(0) for m in _TIME.finditer(_DATETIME.sub(" ", answer))
                   if not any(m.group(0) in s for s in stamps)]
        r.unsupported_timestamps = sorted(set(bad_ts))
        if r.unsupported_timestamps:
            r.issues.append(f"timestamps not in the evidence: {', '.join(r.unsupported_timestamps[:5])}")
        claims = scan_claims(answer)
        r.claim_violations = [{"claim": c.claim, "sentence": c.sentence} for c in claims]
        if claims:
            r.issues.append("prohibited claims asserted: " + ", ".join(sorted({c.claim for c in claims})))
        if CANARY in answer or any(f.lower() in answer.lower() for f in _PROMPT_FRAGMENTS):
            r.issues.append("answer reproduces the system prompt")
        if _SECRET.search(answer) or any(s and s in answer for s in secrets):
            r.issues.append("answer contains secret-like content")
    r.passed = not r.issues
    return r


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove key-like strings from text that is stored (audit) - defence in depth."""
    out = _SECRET.sub("[redacted]", text or "")
    for s in secrets:
        if s:
            out = out.replace(s, "[redacted]")
    return out
