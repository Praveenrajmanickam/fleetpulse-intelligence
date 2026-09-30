"""AnalystService: question -> scope/intent -> Gold evidence -> provider -> validation -> response.

Reliability over creativity: an answer that fails validation is regenerated once with a
corrective note; if it fails again (or the provider errors / times out) the response is a
deterministic safe summary composed from the same evidence, clearly labelled as such."""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from fleetpulse.ai import intents
from fleetpulse.ai.audit import AuditStore, InMemoryAuditStore, make_record
from fleetpulse.ai.compose import compose_answer
from fleetpulse.ai.evidence import EvidenceNotFound, EvidencePackage, EvidenceRetriever
from fleetpulse.ai.policy import POLICY_VERSION
from fleetpulse.ai.prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_user_message
from fleetpulse.ai.providers import AIProvider, ProviderError, ProviderRequest, ProviderUnavailable
from fleetpulse.ai.validation import extract_answer, redact, validate_answer
from fleetpulse.api.repository import GoldRepository

SCOPES = ("event", "session", "overall", "scenario")
MAX_ATTEMPTS = 2
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fleetpulse-ai")


class AnalystRequestError(ValueError):
    """Invalid analyst request (maps to HTTP 422)."""


@dataclass
class AskRequest:
    question: str
    scope: str
    event_id: str | None = None
    session_id: str | None = None
    scenario: dict | None = None  # avoidable_pct, fuel_price_per_l, currency, event_ids


class AnalystService:
    def __init__(self, repo: GoldRepository, provider: AIProvider | None, audit: AuditStore | None = None,
                 timeout_s: float = 30.0, unavailable_reason: str = "No AI provider configured."):
        self.retriever = EvidenceRetriever(repo)
        self.provider = provider
        self.audit = audit or InMemoryAuditStore()
        self.timeout_s = timeout_s
        self.unavailable_reason = unavailable_reason

    # --- status -------------------------------------------------------------------
    def status(self) -> dict:
        base = {"available": self.provider is not None, "policy_version": POLICY_VERSION,
                "prompt_version": PROMPT_VERSION, "max_question_chars": intents.MAX_QUESTION_CHARS,
                "scopes": list(SCOPES)}
        if self.provider is None:
            return {**base, "provider": None, "model": None, "is_mock": False, "message": self.unavailable_reason}
        msg = ("Development mock provider: deterministic template answers, not an LLM."
               if self.provider.is_mock else "Analyst provider configured.")
        return {**base, **self.provider.describe(), "message": msg}

    def _secrets(self) -> tuple[str, ...]:
        get = getattr(self.provider, "_secret", None)
        return (get(),) if callable(get) else ()

    # --- evidence -----------------------------------------------------------------
    def _package(self, req: AskRequest) -> tuple[EvidencePackage, str | None]:
        if req.scope == "event":
            if not req.event_id:
                raise AnalystRequestError("scope 'event' requires event_id")
            return self.retriever.event(req.event_id), None
        if req.scope == "session":
            if not req.session_id:
                raise AnalystRequestError("scope 'session' requires session_id")
            return self.retriever.session(req.session_id), None
        if req.scope == "overall":
            return self.retriever.overall(), None
        if req.scope == "scenario":
            inputs, source = dict(req.scenario or {}), "request"
            if inputs.get("avoidable_pct") is None or inputs.get("fuel_price_per_l") is None:
                parsed = intents.parse_scenario_inputs(req.question)
                for k, v in parsed.items():
                    if inputs.get(k) is None:
                        inputs[k] = v
                source = "parsed_from_question" if parsed else source
            if inputs.get("avoidable_pct") is None or inputs.get("fuel_price_per_l") is None:
                raise AnalystRequestError("scope 'scenario' needs avoidable_pct and fuel_price_per_l "
                                          "(in the request, or stated in the question, e.g. '30 %' and '1.80 EUR per litre')")
            try:
                pkg = self.retriever.scenario(float(inputs["avoidable_pct"]), float(inputs["fuel_price_per_l"]),
                                              str(inputs.get("currency") or "EUR"), inputs.get("event_ids") or None)
            except ValueError as exc:
                raise AnalystRequestError(str(exc)) from None
            return pkg, source
        raise AnalystRequestError(f"scope must be one of {SCOPES}")

    # --- main ---------------------------------------------------------------------
    def ask(self, req: AskRequest) -> dict:
        t0 = time.perf_counter()
        request_id = f"ai-{uuid.uuid4().hex[:16]}"
        if req.scope not in SCOPES:
            raise AnalystRequestError(f"scope must be one of {SCOPES}")
        if not req.question or not req.question.strip():
            raise AnalystRequestError("question must not be empty")
        if len(req.question) > intents.MAX_QUESTION_CHARS:
            raise AnalystRequestError(f"question longer than {intents.MAX_QUESTION_CHARS} characters")
        secrets = self._secrets()
        question = redact(intents.sanitize_question(req.question), secrets)  # secrets never reach the provider
        flags = intents.injection_flags(req.question)
        intent = intents.classify(question, req.scope)
        audit_base = dict(request_id=request_id, scope=req.scope, subject_id=req.event_id or req.session_id,
                          intent=intent, question=question, input_flags=flags,
                          policy_version=POLICY_VERSION, prompt_version=PROMPT_VERSION)

        if self.provider is None:
            self.audit.write(make_record(**audit_base, timestamp_utc=_now(), status="provider_unavailable",
                                         provider=None, model=None, is_mock=False))
            raise ProviderUnavailable(self.unavailable_reason)

        package, scenario_source = self._package(req)  # EvidenceNotFound / AnalystRequestError propagate
        attempts, note, answer, source, status, issues = 0, None, None, None, None, []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            attempts = attempt
            preq = ProviderRequest(system=SYSTEM_PROMPT, user=build_user_message(package, question, flags, note),
                                   scope=req.scope, intent=intent, package=package, flags=tuple(flags),
                                   corrective_note=note)
            try:
                reply = self._call(preq)
            except ProviderError as exc:
                issues.append(str(exc))
                status = "fallback_provider_error"
                break
            candidate = extract_answer(reply.text)
            result = validate_answer(candidate, package, secrets)
            if result.passed:
                answer = candidate
                source = "mock" if self.provider.is_mock else "llm"
                status = "answered" if attempt == 1 else "answered_after_correction"
                break
            issues.extend(result.issues)
            note = result.corrective_note()
            status = "fallback_validation_failed"

        if answer is None:
            answer = compose_answer(package, intent, flags)
            if not validate_answer(answer, package, secrets).passed:  # should not happen; last-resort listing
                answer = " ".join(f"{f.label}: {f.display}." for f in package.facts)
            source = "deterministic_fallback"

        response = {
            "request_id": request_id,
            "status": status,
            "answer": answer,
            "answer_source": source,
            "scope": req.scope,
            "subject_id": package.subject_id,
            "intent": intent,
            "evidence_ids": package.evidence_ids,
            "measured_facts": [asdict(f) for f in package.facts],
            "fleetpulse_interpretation": package.interpretation,
            "limitations": package.limitations,
            "suggested_investigations": package.investigations,
            "confidence": package.confidence,
            "scenario": package.scenario,
            "scenario_inputs_source": scenario_source,
            "gold_build_id": package.gold_build_id,
            "rule_version": package.rule_version,
            "provider": self.provider.name,
            "model": self.provider.model,
            "is_mock": self.provider.is_mock,
            "generated_at": _now(),
            "validation": {"passed": source != "deterministic_fallback", "attempts": attempts,
                           "issues": [redact(i, secrets) for i in issues]},
            "input_flags": flags,
            "evidence_context": package.context,
            "policy_version": POLICY_VERSION,
        }
        self.audit.write(make_record(
            **audit_base, timestamp_utc=response["generated_at"], evidence_ids=package.evidence_ids,
            gold_build_id=package.gold_build_id, rule_version=package.rule_version, provider=self.provider.name,
            model=self.provider.model, is_mock=self.provider.is_mock, status=status, answer_source=source,
            validation_passed=response["validation"]["passed"], validation_issues=response["validation"]["issues"],
            attempts=attempts, latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            answer=redact(answer, secrets)))
        return response

    def _call(self, preq: ProviderRequest):
        fut = _EXECUTOR.submit(self.provider.generate, preq, self.timeout_s)
        try:
            return fut.result(timeout=self.timeout_s)
        except FutureTimeout:
            fut.cancel()
            raise ProviderError(f"{self.provider.name}: timeout after {self.timeout_s:g} s") from None
        except ProviderError:
            raise
        except Exception as exc:  # provider bug: never break the dashboard
            raise ProviderError(f"{self.provider.name}: {type(exc).__name__}") from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = ["AnalystService", "AskRequest", "AnalystRequestError", "EvidenceNotFound", "ProviderUnavailable", "SCOPES"]
