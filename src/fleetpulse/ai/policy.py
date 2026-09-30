"""Machine-readable claim policy for the AI Analyst (claims-v1).

A prohibited claim is flagged only when it is *asserted*: a sentence that mentions a
prohibited concept together with a negation / limitation cue ("cannot", "does not",
"no evidence", ...) is a correct refusal, not a claim. Scenario sentences may use the
operator's "avoidable share" wording when they are marked as scenario / assumption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

POLICY_VERSION = "claims-v1"

ALLOWED_VOCABULARY = [
    "observed", "detected", "measured", "stationary engine-on operation",
    "fuel consumed during the observed state", "extended event", "investigation recommended",
    "scenario estimate",
]

PROHIBITED_CLAIMS: list[dict] = [
    {"id": "fuel_waste", "description": "calling observed fuel waste / wasted",
     "patterns": [r"\bwast(e|ed|eful|ing)\b"]},
    {"id": "avoidable_idling", "description": "stating that idling was avoidable or unnecessary",
     "patterns": [r"\bavoidable\b", r"\bunnecessar(y|ily)\b", r"\bexcessive idl\w*"], "scenario_exempt": True},
    {"id": "driver_fault", "description": "attributing the event to the driver (fault, blame, responsibility)",
     "patterns": [r"\bdriver'?s?\W+(\w+\W+)?(fault|error|blame|responsib\w*|misuse|negligen\w*|habit\w*|behaviou?r)\b",
                  r"\bblame\w*\b", r"\bdriver (left|kept|let) the engine\b"]},
    {"id": "driver_inefficiency", "description": "judging driver or vehicle efficiency",
     "patterns": [r"\binefficien(t|cy|cies)\b", r"\bpoor driving\b", r"\bbad (driving|habits?)\b"]},
    {"id": "cost_savings", "description": "claiming savings or money that could be saved",
     "patterns": [r"\bsav(e|ed|es|ing|ings)\b"], "scenario_exempt": True},
    {"id": "mechanical_fault", "description": "diagnosing a mechanical or engine fault",
     "patterns": [r"\b(mechanical|engine|component|sensor)\s+(fault|failure|problem|defect|malfunction|issue)s?\b",
                  r"\bmalfunction\w*\b"]},
    {"id": "cause_of_stop", "description": "stating why the vehicle was stationary",
     "patterns": [r"\b(was|were)\s+(loading|unloading|waiting|queu\w*|in traffic|on (a )?break|warming up|refuel\w*|parked for)\b",
                  r"\b(stopped|stationary|idl\w*|stood)\s+(because|due to|as a result of)\b",
                  r"\b(the )?(cause|reason) (of|for) the stop (was|is)\b"]},
    {"id": "fleet_wide_behaviour", "description": "generalising to a fleet or to other vehicles",
     "patterns": [r"\bfleet[- ]?wide\b", r"\bacross the fleet\b", r"\ball (vehicles|trucks)\b", r"\bevery vehicle\b",
                  r"\b(the|your) fleet (is|was|has|shows)\b", r"\b(compared (to|with)|than) other (vehicles|trucks)\b"]},
    {"id": "predictive_maintenance", "description": "maintenance predictions or conclusions",
     "patterns": [r"\bpredict\w*\s+(maintenance|failure)\b", r"\bmaintenance (is |will be )?(needed|required|due)\b",
                  r"\bwill (fail|break)\b", r"\bneeds? (a )?(service|repair)\b"]},
    {"id": "location_or_activity", "description": "asserting a location or activity",
     "patterns": [r"\bat (the|a) (depot|customer|warehouse|terminal|yard|site|loading dock)\b"]},
]

NEGATION_CUES = re.compile(
    r"\b(not|no|cannot|can't|can ?not|does ?n[o']t|doesn't|do not|don't|isn't|is not|aren't|without|unsupported|"
    r"whether|unable|neither|nor|insufficient|unknown|would require|never|none|no evidence|not supported|refrain|avoid (calling|stating|claiming))\b",
    re.IGNORECASE,
)
SCENARIO_CUES = re.compile(r"\b(scenario|assum\w*|operator[- ]selected|hypothetical|if\b|estimate)", re.IGNORECASE)

_COMPILED = [(c["id"], [re.compile(p, re.IGNORECASE) for p in c["patterns"]], c.get("scenario_exempt", False))
             for c in PROHIBITED_CLAIMS]
# sentences and contrastive clauses ("not X, but Y" must not hide Y behind the "not")
_SENTENCE = re.compile(r"(?<=[.!?;:])\s+|\n+|,?\s+but\s+|,?\s+however,?\s+|\s[—–]\s", re.IGNORECASE)


@dataclass(frozen=True)
class ClaimViolation:
    claim: str
    sentence: str


def scan_claims(text: str) -> list[ClaimViolation]:
    """Sentences that assert a prohibited claim (mention without negation / scenario marking)."""
    out: list[ClaimViolation] = []
    for sentence in (s.strip() for s in _SENTENCE.split(text or "")):
        if not sentence:
            continue
        negated = bool(NEGATION_CUES.search(sentence))
        scenario = bool(SCENARIO_CUES.search(sentence))
        for claim_id, patterns, scenario_exempt in _COMPILED:
            if any(p.search(sentence) for p in patterns):
                if negated or (scenario_exempt and scenario):
                    continue
                out.append(ClaimViolation(claim_id, sentence[:240]))
    return out


def policy_document() -> dict:
    return {
        "version": POLICY_VERSION,
        "principle": "The LLM explains. FleetPulse computes.",
        "allowed_vocabulary": ALLOWED_VOCABULARY,
        "prohibited_claims": [{k: v for k, v in c.items()} for c in PROHIBITED_CLAIMS],
        "assertion_rule": "a prohibited concept is allowed only inside a negated/limiting sentence "
                          "(e.g. 'the telemetry cannot show this was fuel waste'); scenario-exempt claims may "
                          "appear in sentences explicitly marked as scenario / assumption",
        "unlock_condition": "future data or evidence that explicitly supports the claim; the current dataset does not",
    }
