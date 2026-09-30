# FleetPulse AI Analyst V1

> **The AI Analyst is an explanatory layer over deterministic FleetPulse analytics. It is not the measurement engine.**
>
> The LLM explains. FleetPulse computes.

The AI Analyst never becomes the source of truth for vehicle metrics:

- Every number an answer may contain comes from Gold (or the Gold scenario engine).
- Every number is checked against that evidence before the answer is shown.
- If the check fails, the answer is regenerated once and then replaced by a deterministic summary.

## 1. Architecture

```
User question (untrusted)
      │
POST /api/v1/ai/ask ──────────────────────────────── fleetpulse.api.app
      │  pydantic contract: scope ∈ {event, session, overall, scenario}, ids, ≤ 1000 chars, no extra fields
      ▼
Intent / scope resolution ─────────────────────────── ai/intents.py   (keyword intents, injection flags, scenario-input parsing)
      ▼
Evidence retrieval from Gold ──────────────────────── ai/evidence.py  (EvidencePackage: facts, interpretation, limitations,
      │                                                                 investigations, confidence, lineage, compact context)
      │   scenario: gold.scenario.idle_scenario runs FIRST; only its result is passed on
      ▼
Prompt / evidence envelope ────────────────────────── ai/prompt.py    (system policy + <evidence> JSON + <user_question>)
      ▼
AIProvider.generate (timeout) ─────────────────────── ai/providers.py (mock | anthropic | openai_compatible)
      ▼
Answer validation ─────────────────────────────────── ai/validation.py (numbers, timestamps, claims, leakage)
      │  fail → regenerate once with a corrective note → fail → deterministic summary (ai/compose.py)
      ▼
Structured response + audit record ────────────────── ai/service.py, ai/audit.py
      ▼
Dashboard analyst panel ───────────────────────────── frontend/src/components/AnalystPanel.tsx
```

- **No vector database and no embeddings.** Gold is already structured, so retrieval is deterministic lookup by scope and ID.
- **Stateless.** Each question is answered only from the evidence for its scope. There is no conversation memory across questions.

## 2. Why Gold, not raw telemetry, goes to the LLM

- **Correctness.** Raw CAN frames and 100 ms telemetry need decoding, freshness checks, state rules and integration. FleetPulse already does that deterministically and tests it (Bronze → Silver → Gold, 246 tests before this phase). An LLM doing it again would be a second, unvalidated measurement engine.
- **Verifiability.** A compact evidence package (≈ 2–6 KB) contains every value the answer may use, so each number can be checked mechanically. That would be impossible against megabytes of telemetry.
- **Scope honesty.** Gold carries the interpretation status (`context_unknown`), the limitations and the claim policy. Raw data carries none of that.
- **Cost and privacy.** Small prompts, and no bulk data leaves the system.

The evidence packages never contain raw CAN frames, Silver tables or timeline points; a test enforces this.

## 3. Provider abstraction

`fleetpulse.ai.providers.AIProvider` is the only interface the application uses:

```python
class AIProvider(ABC):
    name: str; is_mock: bool; model: str
    def generate(self, request: ProviderRequest, timeout_s: float) -> ProviderReply: ...
```

| Implementation | Purpose |
|---|---|
| `MockAIProvider` | Deterministic development provider with **no LLM**. It composes answers from the evidence package with fixed templates (`ai/compose.py`), and is marked everywhere: `provider: "mock"`, `is_mock: true`, `answer_source: "mock"`, and the UI label "Development mock · not an LLM". |
| `AnthropicProvider` | Messages API over `httpx` (no SDK dependency). The default model is `claude-opus-5-5`. |
| `OpenAICompatibleProvider` | Any `/chat/completions`-compatible endpoint (`FLEETPULSE_AI_BASE_URL`). |

Configuration is environment-only (see `.env.example`):

| Variable | Meaning |
|---|---|
| `FLEETPULSE_AI_PROVIDER` | `none` (default) \| `mock` \| `anthropic` \| `openai_compatible` |
| `FLEETPULSE_AI_MODEL` | model name (provider default if empty) |
| `FLEETPULSE_AI_API_KEY` | secret, **server side only** |
| `FLEETPULSE_AI_BASE_URL` | endpoint (required for `openai_compatible`) |
| `FLEETPULSE_AI_TIMEOUT_S` | per-call timeout (default 30 s) |
| `FLEETPULSE_AI_AUDIT_PATH` | JSONL audit file (default `data/ai_audit/ai_audit.jsonl`, git-ignored) |

**No provider configured.**
- `GET /ai/status` returns `available: false` with a reason, for example "Provider 'anthropic' configured but FLEETPULSE_AI_API_KEY is not set."
- `POST /ai/ask` returns **503** `{"status": "provider_unavailable"}`.
- The dashboard, the Gold endpoints and the deterministic `GET /ai/evidence/{event_id}` keep working.
- The analyst panel disables its input and says why.

Adding a vendor means one new `AIProvider` subclass plus one branch in `resolve_provider`; nothing else changes.

## 4. Evidence retrieval (`ai/evidence.py`)

| Scope | Retrieved | Evidence IDs |
|---|---|---|
| `event` | the Gold event row and its evidence object (stored, or generated by `gold.evidence`); duration, fuel, fuel rates, RPM, load, coolant, neighbouring states, tags, time range, rule/version and thresholds, detection confidence, interpretation status, limitations, session context | `event:`, `evidence:`, `session:` |
| `session` | Gold session summary (engine-on, moving, stationary time and share, distance, fuel, stationary fuel, events, longest event); top 5 events by duration and by fuel; extended-event count and fuel | `session:` plus the listed `event:`s |
| `overall` | Gold operating summary, session counts (17 / 46 / 18 / 11), duration distribution, attention groups, the 3 events over 15 min, top 3 sessions by stationary time, and extended-event shares | `operating_summary:<gold_build_id>`, `event:`s, `session:`s |
| `scenario` | **`gold.scenario.idle_scenario` result** (basis fuel, operator share and price, scenario fuel, cost exposure, label) | basis IDs plus `scenario_engine:fleetpulse.gold.scenario.idle_scenario` |

- **Facts.** Each fact has an `id`, `label`, `value`, `unit`, a fixed `display` string and a `source`: `gold`, `derived_from_gold` or `gold_scenario_engine`.
- **Derived figures.** A few figures are sums or shares over pre-classified Gold event rows, for example "extended events account for 30.9 % of stationary engine-on time". FleetPulse computes them in the retriever and labels them `derived_from_gold`. The LLM never computes them.
- **Scenario inputs.** They come from the request's `scenario` object, or FleetPulse parses them deterministically from the question ("30 %", "1.80 EUR per litre"). The response reports which (`scenario_inputs_source`).
- **Missing inputs.** A question like "diesel costs X" returns 422: there are no defaults and nothing is guessed.

## 5. Claim policy (`ai/policy.py`, `GET /api/v1/ai/policy`)

The policy is machine-readable, version `claims-v1`.

- **Allowed vocabulary:** observed, detected, measured, stationary engine-on operation, fuel consumed during the observed state, extended event, investigation recommended, scenario estimate.
- **Prohibited as fact** (each has regex patterns):
  - `fuel_waste`
  - `avoidable_idling`
  - `driver_fault`
  - `driver_inefficiency`
  - `cost_savings`
  - `mechanical_fault`
  - `cause_of_stop`
  - `fleet_wide_behaviour`
  - `predictive_maintenance`
  - `location_or_activity`
- **Assertion rule.** A prohibited concept may appear only in a negated or limiting clause. For example, "The evidence cannot support calling this fuel waste" is correct and required when the user asks.
  - Clauses are split on sentences and on contrastive "but / however / —", so "not X, but it was fuel waste" is still caught.
  - `avoidable_idling` and `cost_savings` are exempt only in sentences explicitly marked as scenario or assumption.
- **Unlock condition.** Only future data or evidence that explicitly supports a claim (for example job, location or PTO context) could unlock it. The current dataset does not.

## 6. Uncertainty separation

Every response separates:
- **MEASURED:** `measured_facts` (Gold values with display strings).
- **INTERPRETATION:** `fleetpulse_interpretation` (deterministic classification: detection rule, extended/highlighted, tags as measured facts), and the free-text `answer`, which the UI labels "AI interpretation".
- **LIMITATIONS:** `limitations`.
- **POSSIBLE NEXT INVESTIGATION:** `suggested_investigations` (location, job/dispatch, PTO/auxiliary state, operations log).
- **CONFIDENCE:** `{detection, interpretation}` (e.g. `high` / `context_unknown`).

Measured facts, limitations and investigations are generated by FleetPulse, not the model. The model contributes only the explanatory `answer` text, which is validated.

## 7. Prompt injection and the data boundary

- **Input limits.** The question must be non-empty and at most 1,000 characters. Pydantic `extra="forbid"` rejects smuggled fields such as `"system": …`, and IDs are limited to 300 characters.
- **Sanitising.** Control characters are dropped. Delimiter tags (`<evidence>`, `<user_question>`, `<system>`) are neutralised. Secret-like strings and the configured key are redacted **before** the question reaches the provider.
- **Separation.** The system prompt holds the rules. The user message carries `<evidence>` (JSON), an optional `<input_notice>` and `<correction>`, and finally `<user_question>`, which is declared untrusted data.
- **Flags.** Deterministic patterns detect: override_instructions, reveal_prompt, reveal_secret, role_play, alter_values, invent_data, markup_escape.
  - Flags are passed to the provider as a notice, returned as `input_flags` and audited.
  - The mock answers flagged questions with a boundary statement and then the evidence.
- **Output checks.** Every answer is validated whatever the input, so an injected "say the fuel was 12 L" fails numeric validation. A reply containing the system-prompt canary or fragments, a `sk-…`/Bearer-like string or the configured key is rejected.

## 8. Numeric validation (`ai/validation.py`)

**Numbers:**
- Every numeric token in the answer is extracted, after removing IDs, dates/times and list enumerations.
- The token must match an evidence value **of the same unit class**: km, km/h, L, L/h, rpm, °C, %, time, money or count.
- Precision follows the answer: "0.63 L" matches 0.626 L, but "0.9 L" does not.
- Time renderings are expanded: seconds ↔ minutes ↔ hours, and the "19m 29s" components.
- A bare number without a unit may match any evidence value.
- Because matching is unit-aware, "700 km" is rejected even though 700 rpm (the raised-idle threshold) is in the evidence.

**Other checks:**
- **Timestamps:** dates and clock times must occur in the evidence.
- **Protected quantities:** duration, fuel, distance, RPM, temperature, session and event counts, and percentages (all tested).
- **Claims:** the claim policy scan above.
- **Leakage and shape:** system-prompt canary or fragments, secret-like strings, the configured key; empty or malformed replies; answers over 3,000 characters.

## 9. Fallback behaviour

| Situation | Result `status` | `answer_source` |
|---|---|---|
| Answer valid on the first attempt | `answered` | `llm` / `mock` |
| Invalid, then valid after one corrective retry (the retry prompt lists the issues) | `answered_after_correction` | `llm` / `mock` |
| Invalid twice | `fallback_validation_failed` | `deterministic_fallback` |
| Provider timeout, HTTP error, transport error, malformed reply or exception | `fallback_provider_error` | `deterministic_fallback` |
| No provider configured | HTTP 503 `provider_unavailable` | — |

- **The fallback** is a template composed from the same evidence (`ai/compose.py`), with intent-aware wording. It passes the same validation, which is tested for all intents.
- **Rejected answers are never shown.** The `validation.issues` list says why an answer was replaced.

## 10. Security boundaries

- **Keys** are read from the environment on the server only. They are:
  - held in a name-mangled attribute, excluded from `repr` and `describe()`;
  - never returned by `/ai/status`;
  - never logged or written to audit;
  - redacted from questions and answers.
- **The frontend** calls only `/api/v1/ai/*`. It contains no keys or vendor endpoints (tested).
- **Files:** `.env` is git-ignored, and `.env.example` holds placeholders only (tested). `data/ai_audit/` is git-ignored.
- **Nothing is exposed** in the way of hidden prompts or chain-of-thought. The response contains the answer, the deterministic evidence package (`evidence_context`), the validation outcome and metadata.

## 11. Auditability (`ai/audit.py`)

Every request, including `provider_unavailable`, writes one record through the replaceable `AuditStore` interface. `JsonlAuditStore` is the default and `InMemoryAuditStore` is used in tests. A database or Delta implementation is a drop-in replacement.

| Recorded | Not recorded |
|---|---|
| request_id, timestamp_utc, scope, subject_id, intent, question (redacted), input_flags | API keys or secrets |
| evidence_ids, gold_build_id, rule_version, policy_version, prompt_version | the system prompt or evidence envelope |
| provider, model, is_mock, status, answer_source | chain-of-thought / reasoning |
| validation_passed, validation_issues, attempts, latency_ms, final answer | rejected model answers |

`GET /api/v1/ai/audit?limit=N` lists recent records. It is a local development aid and has no authentication.

## 12. API

| Method & path | Purpose |
|---|---|
| `POST /api/v1/ai/ask` | Ask a question. The body is `{question, scope, event_id?, session_id?, scenario?: {avoidable_pct?, fuel_price_per_l?, currency?, event_ids?}}`. Errors: 422 (invalid scope, missing ID, empty or oversized question, extra fields, missing scenario inputs); 404 (unknown event, session or scenario event IDs); 503 (provider unavailable). |
| `GET /api/v1/ai/status` | availability, provider, model, is_mock, message, policy and prompt versions, maximum question length |
| `GET /api/v1/ai/policy` | the machine-readable claim policy |
| `GET /api/v1/ai/audit` | recent audit records |
| `GET /api/v1/ai/evidence/{event_id}` | **unchanged**: deterministic evidence object, independent of any provider |

**Response contract:**
- **Answer and outcome:** `request_id`, `status`, `answer`, `answer_source`, `validation {passed, attempts, issues}`.
- **Scope:** `scope`, `subject_id`, `intent`, `input_flags`.
- **Evidence:** `evidence_ids`, `measured_facts[]`, `fleetpulse_interpretation[]`, `limitations[]`, `suggested_investigations[]`, `confidence {detection, interpretation}`, `evidence_context`.
- **Scenario:** `scenario` (the Gold result or null), `scenario_inputs_source`.
- **Lineage and provider:** `gold_build_id`, `rule_version`, `policy_version`, `provider`, `model`, `is_mock`, `generated_at`.

## 13. Supported questions (V1)

| Scope | Question types (intent) |
|---|---|
| event | what happened (explain), how much fuel (fuel), why flagged (why_flagged), "was this fuel waste / avoidable" (prohibited_waste), "was the driver responsible" (prohibited_driver), "why did it stop" (prohibited_cause), what data would help (additional_data) |
| session | summary, how much stationary operation (stationary_amount), which events to investigate (investigate), where stationary fuel was concentrated (concentration) |
| overall | vehicle summary, strongest finding, stationary fuel, how common extended events are, what to investigate first, fleet-wide questions (answered as one-vehicle only) |
| scenario | "If 30 % were avoidable and diesel costs 1.80 EUR, what is the exposure?" The Gold engine calculates; the answer explains. |

Intent classification is a small deterministic keyword table. It steers the mock and fallback templates and is audited. A real LLM receives the full question and evidence regardless of intent.

## 14. Frontend

- **Where it opens.** "Ask FleetPulse AI" opens the analyst drawer on Event Detail (event scope), Session Detail (session), Overview and Idle Intelligence (overall), and Impact Scenario ("Explain with FleetPulse AI", after a Gold scenario run). The `/ai` page embeds the panel with the overall scope.
- **Header.** "FleetPulse AI Analyst · Evidence-grounded vehicle intelligence", with a scope pill and a provider badge (mock, provider/model, or unavailable).
- **Suggested questions** depend on the scope.
- **Answer card sections:**
  - "AI interpretation" (or "Mock analyst answer · development output, not an LLM", or "FleetPulse deterministic summary");
  - the validation status;
  - **"Measured by FleetPulse"** (facts with Gold / derived / scenario-engine provenance);
  - limitations and possible next investigation;
  - **"Inspect evidence used"**: linked evidence IDs, FleetPulse classification, build, rule and policy, provider, validation issues, input flags, and the raw evidence package JSON.
- **Deep links:** `?ask=1&q=…` on an event page, or `/ai?q=…`.
- **Screenshots:** `reports/dashboard_v1/10_ai_event_waste.png` and `11_ai_overall.png`, captured with the mock provider.

## 15. Running

```bash
cp .env.example .env            # choose FLEETPULSE_AI_PROVIDER=mock for development (no API cost)
set -a; source .env; set +a
uvicorn fleetpulse.api.app:app --port 8000
```

With `FLEETPULSE_AI_PROVIDER` unset, everything runs and the analyst reports itself unavailable.

## 16. Tests (`tests/test_ai.py`, 109 tests)

No test makes a network call. The real providers are tested through `httpx.MockTransport`, and the scripted, failing and slow fake providers exercise the service. The tests cover:
- evidence retrieval for event, session, overall and scenario, and that the packages stay compact with no telemetry;
- the scenario is routed through `gold.scenario.idle_scenario` (verified by spy);
- missing IDs, invalid scope, empty, whitespace-only and oversized questions, and extra fields;
- provider unavailable (service and API), provider exceptions, timeout (thread and HTTP), HTTP 401/500/529, and malformed replies;
- 13 prohibited-claim assertions caught, and 7 negated or scenario statements allowed;
- hallucinated numbers and timestamps (fuel, duration, RPM, temperature, distance, counts, percentages, dates, times);
- unit-aware matching;
- regenerate-then-pass, and fallback after repeated failure;
- the 7 injection flag types, delimiter sanitising, and that an injection can neither change values nor leak the prompt;
- key never in responses, audit or status, and redacted before reaching the provider; `.env.example` placeholders; no keys in the frontend;
- audit record fields and the JSONL store;
- mock provider and composer validity for every intent, and intent classification for all V1 example questions;
- the API response contract.

## 17. Known limitations

- **No real LLM has been exercised yet.** The HTTP providers are contract-tested against mocked transports only; real model behaviour (answer style, corrective retries) is unmeasured.
- **Keyword intent routing** only shapes the mock and fallback templates. Unusual phrasings get the generic explain or summary template.
- **Numeric validation is lexical.** It proves each number exists in the evidence with a compatible unit, not that it is attached to the right quantity. Two cases remain:
  - "0.626 L at the start of the session" would pass;
  - unitless numbers may match any evidence value.
  The deterministic measured-facts table is always shown beside the answer for this reason.
- **The claim policy is pattern-based.** Paraphrases outside the patterns can slip through, and some legitimate sentences may be over-blocked; the negation rule is heuristic.
- **Stateless:** there are no follow-up questions with memory.
- **English only.**
- **The audit store is a local JSONL file** without rotation or access control. The `/ai/audit` endpoint has no authentication (same as the whole V1 API).
- **Same one-vehicle, Part 3, `context_unknown` constraints as Gold.** The analyst cannot explain why a vehicle was stationary, because that information is not in the data.
