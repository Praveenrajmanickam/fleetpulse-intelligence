"""Deterministic question handling: intent classification, prompt-injection flags and
scenario-input parsing. User questions are untrusted input."""

from __future__ import annotations

import re

MAX_QUESTION_CHARS = 1000

# (intent, pattern) in priority order, per scope; the first match wins.
_INTENTS: dict[str, list[tuple[str, str]]] = {
    "event": [
        ("prohibited_waste", r"\bwast|avoidab|unnecessar|sav(e|ing)|could (we|they) have"),
        ("prohibited_driver", r"\bdriver|responsib|fault|blame|who\b"),
        ("prohibited_cause", r"\bwhy (was|did) (the )?(vehicle|truck) (stop|stand|idle|stationary)|\bcause\b|\breason\b"),
        ("additional_data", r"additional data|more data|what data|what (else|information)|help explain|context"),
        ("why_flagged", r"\bwhy\b.*\b(flag|detect|classif|highlight)|\bflag|\brule\b"),
        ("fuel", r"\bfuel|litre|liter|consum"),
        ("explain", r".*"),
    ],
    "session": [
        ("prohibited_waste", r"\bwast|avoidab|unnecessar|sav(e|ing)"),
        ("prohibited_driver", r"\bdriver|responsib|fault|blame"),
        ("concentration", r"concentrat|where .*fuel|most fuel|which events .*fuel"),
        ("investigate", r"investigat|which events|look at|review|should i"),
        ("stationary_amount", r"how much stationary|stationary (time|operation)|how long .*stationary|idle time"),
        ("summary", r".*"),
    ],
    "overall": [
        ("prohibited_waste", r"\bwast|avoidab|unnecessar|sav(e|ing)"),
        ("prohibited_driver", r"\bdriver|responsib|fault|blame"),
        ("prohibited_fleet", r"\bfleet[- ]?wide|other (vehicles|trucks)|whole fleet|all (vehicles|trucks)"),
        ("investigate_first", r"investigat|first|priorit|fleet manager|should .* (look|check)"),
        ("extended_frequency", r"\bextended|how (common|often|many) .*(long|extended)|over 15|> ?15"),
        ("strongest_finding", r"strongest|key finding|main finding|most important|headline"),
        ("stationary_fuel", r"\bfuel"),
        ("summary", r".*"),
    ],
    "scenario": [("explain", r".*")],
}
_COMPILED = {s: [(i, re.compile(p, re.IGNORECASE)) for i, p in v] for s, v in _INTENTS.items()}


def classify(question: str, scope: str) -> str:
    for intent, pat in _COMPILED[scope]:
        if pat.search(question):
            return intent
    return "explain"


INJECTION_PATTERNS = {
    "override_instructions": r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instruction|rule|policy|evidence|previous|above|system|guard)",
    "reveal_prompt": r"\b(reveal|show|print|repeat|output|tell me|what is|display)\b.{0,40}\b(system prompt|prompt|instructions|hidden)",
    "reveal_secret": r"\b(api[_ -]?key|secret|token|password|credential|env(ironment)? var)",
    "role_play": r"\b(you are now|act as|pretend|roleplay|role-play|developer mode|jailbreak|DAN)\b",
    "alter_values": r"\b(change|set|make|pretend|assume|say)\b.{0,40}\b(fuel|duration|rpm|distance|value|number|gold)\b.{0,30}\b(to|is|was|=)\s*\d",
    "invent_data": r"\b(invent|make up|fabricate|hallucinate|guess)\b",
    "markup_escape": r"</?\s*(evidence|user_question|system)\b",
}
_INJ = {k: re.compile(v, re.IGNORECASE) for k, v in INJECTION_PATTERNS.items()}


def injection_flags(question: str) -> list[str]:
    return [k for k, p in _INJ.items() if p.search(question)]


def sanitize_question(question: str) -> str:
    """Normalise whitespace, drop control characters and neutralise delimiter tags."""
    q = "".join(ch for ch in question if ch == "\n" or ch >= " ")
    q = re.sub(r"<\s*/?\s*(evidence|user_question|system)[^>]*>", "[removed tag]", q, flags=re.IGNORECASE)
    return q.strip()[:MAX_QUESTION_CHARS]


_PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*(?:%|percent|per ?cent)", re.IGNORECASE)
_PRICE = re.compile(
    r"(?:(?:€|eur|\$|usd|£|gbp)\s*(\d+(?:[.,]\d+)?))|(?:(\d+(?:[.,]\d+)?)\s*(?:€|eur(?:os?)?|\$|usd|£|gbp|per ?lit(?:re|er)|/\s*l\b))",
    re.IGNORECASE)
_CURRENCY = {"€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP"}


def parse_scenario_inputs(question: str) -> dict:
    """Deterministically read '30 %' and a fuel price ('1.80 EUR', '€1.8', '1.8 per litre') from a question.
    Returns the found keys only; FleetPulse (not the LLM) interprets the inputs."""
    out: dict = {}
    m = _PCT.search(question)
    if m:
        out["avoidable_pct"] = float(m.group(1).replace(",", "."))
    for m in _PRICE.finditer(question):
        raw = m.group(1) or m.group(2)
        if raw is None:
            continue
        start = m.start()
        if _PCT.search(question[start:start + len(m.group(0)) + 2]):
            continue
        out["fuel_price_per_l"] = float(raw.replace(",", "."))
        token = re.search(r"€|\$|£|eur(?:os?)?|usd|gbp", m.group(0), re.IGNORECASE)
        if token:
            out["currency"] = _CURRENCY.get(token.group(0).lower(), "EUR")
        break
    return out
