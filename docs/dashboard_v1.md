# FleetPulse Dashboard V1

**Connected Vehicle Operations & Efficiency Intelligence**. Solution Module #1: **Idle Intelligence**.

Dashboard V1 is the first product interface on top of the Gold layer (see
[gold_idle_intelligence.md](gold_idle_intelligence.md) and the serving contract in
[dashboard_contract_v1.md](dashboard_contract_v1.md)). It covers **one vehicle** (Turku Renault T520) during the
**Part 3 observation window**. It is not a fleet-wide measurement.

## 1. Architecture

```
Silver (100 ms telemetry)  ─┐
                            ├─►  Gold (idle-v1)  ─►  FastAPI  /api/v1/*  ─►  React (Vite)
Gold tables + evidence  ────┘         ▲                    │
                                      └── gold.scenario ◄──┘  (POST /idle/scenario)
```

| Layer | Responsibility | Does not do |
|---|---|---|
| Gold (`data/gold_part3/gold`) | the source of truth: events, session summaries, operating summary, evidence objects, manifest | — (unchanged in this phase) |
| `fleetpulse.api.repository` | read-only loading of Gold tables (cached); Silver telemetry for timelines (row selection plus display sampling) | aggregate or recompute metrics |
| `fleetpulse.api.serving` | documented presentation rules (engine-operation scope, attention groups) and fixed wording | derive vehicle metrics |
| `fleetpulse.api.app` | FastAPI endpoints: filter, paginate and sort Gold rows; the scenario is delegated to `fleetpulse.gold.scenario.idle_scenario` | calculate KPIs |
| `frontend/` | formatting, charts and navigation | calculate metrics (only unit display, e.g. s → h, and bar geometry) |

### Presentation rules (the only logic in the serving layer)

- **Engine-operation sessions.** These are recording sessions with ≥ 0.1 h (360 s) of engine-on time, matching the scope definition of the Part 3 Gold build. There are 17 such sessions.
  - Gold also contains one 40 s engine-on fragment (2021-02-03 06:28:56; 33.6 s engine-on, 1 event, 0.098 L). It is included in every Gold total, and the Overview lists it explicitly.
  - The "11 / 17" KPI combines Gold's `sessions_with_over_15_min_stationary` (11, meaning accumulated stationary engine-on time > 15 min) with the count of engine-operation sessions.
- **Attention groups.** These group Gold's pre-computed `duration_class` values:
  - *Routine / short observations* are events under 5 min (`under_1_min`, `1_to_5_min`): 864 events.
  - *Extended events for investigation* are events of 5 min or more (`5_to_15_min`, `over_15_min`): 16 events.
  - Being in the extended group is not a judgement about the event.
- **Fixed statements.** The detection statement (chosen by `detection_confidence`), the interpretation statement ("The available telemetry does not identify why the vehicle remained stationary."), the module statement and the scope statement are all constants. Tests check them for prohibited terminology.
- **Timeline sampling.** The API returns every k-th 100 ms Silver grid row, with no averaging and at most 1,500 points (event) or 2,000 (session). Nulls stay null and are drawn as gaps. The API response states the sampling step.

## 2. Setup and running

```bash
conda activate fleetpulse-can
pip install -e ".[api]"            # fastapi, uvicorn, httpx (already installed in fleetpulse-can)

# frontend (Node >= 20)
cd frontend && npm install && npm run build && cd ..

# serve API + built dashboard on one port
uvicorn fleetpulse.api.app:app --port 8000      # run from the repo root
# open http://localhost:8000   (API docs: http://localhost:8000/docs)

# or develop with hot reload: API on :8000, Vite on :5173 (proxies /api)
cd frontend && npm run dev
```

Environment variables:
- `FLEETPULSE_GOLD_ROOT` (default `data/gold_part3/gold`)
- `FLEETPULSE_SILVER_ROOT` (default `data/gold_part3/silver`; without it, timelines report `available: false`)
- `FLEETPULSE_FRONTEND_DIST` (default `frontend/dist`)

The API loads the latest Gold manifest under `_builds/` and caches the tables in memory: about 1 k event rows and 46 session rows. A timeline request reads one session's slice of Silver Parquet with pyarrow filters.

**Frontend stack:**
- Vite 7, React 19, TypeScript 5.8, react-router-dom 7 and Recharts 3.
- IBM Plex Sans/Mono, loaded from Google Fonts.
- No UI framework and no state library.

## 3. API endpoints (`/api/v1`)

| Method & path | Returns | Source |
|---|---|---|
| `GET /overview` | scope, KPIs, session counts (17 / 46 / 18, short fragment), engine-time composition, build | `gold_operating_summary`, `gold_session_summary`, manifest |
| `GET /idle/summary` | duration distribution, attention groups, 16 extended events, 3 highlighted > 15 min events, per-session stationary vs moving time | `gold_idle_events`, `gold_session_summary` |
| `GET /idle/events` | paginated events. Parameters: `page`, `page_size` ≤ 500; filters `session_id`, `duration_class` (repeatable), `attention_group`, `min/max_duration_s`, `min/max_fuel_l`, `cold_engine`, `raised_rpm`, `preceded_by`, `followed_by`; `sort` over `event_start`, `duration_seconds`, `fuel_consumed_l`, `average_fuel_rate_lph`, `average_engine_rpm`, `coolant_start_c` (prefix `-` for descending) | `gold_idle_events` |
| `GET /idle/events/{event_id}` | full event row, detection and interpretation statements, session, and a Silver timeline of ±120 s (`context_s`, clipped to the session). Unknown ID → 404 | Gold, plus Silver telemetry |
| `GET /sessions?scope=engine_operation\|all` | session summaries (17 or 46) | `gold_session_summary` |
| `GET /sessions/{session_id}` | summary, the session's events and the session timeline. Unknown ID → 404 | Gold, plus Silver telemetry |
| `POST /idle/scenario` | the result of `gold.scenario.idle_scenario`, plus the basis, `calculated_by` and the build | see below |
| `GET /ai/evidence/{event_id}` | evidence object: the stored one (20 in `gold_idle_evidence`), or one generated on request with `gold.evidence.evidence_object` using the build's rule config. `provenance` says which. Unknown ID → 404 | Gold evidence |
| `GET /meta`, `GET /health` | build footer, module list, scope | manifest |

**`POST /idle/scenario` body:**
- `avoidable_pct` (0–100) and `fuel_price_per_l` (≥ 0) are **required, with no default**. Missing or out-of-range values → 422.
- `currency` defaults to `EUR`.
- `basis` is `all_stationary` (Gold `stationary_fuel_consumed_l`) or `events`, which uses `event_ids` and sums their Gold `fuel_consumed_l`. Unknown IDs → 404.
- An optional `extrapolation` takes `observed_period_days`, `projection_days` and a required non-empty `assumption_note`.
- The response label is `SCENARIO / ESTIMATE - not observed savings`.

## 4. Screens

Screenshots are headless Chrome captures at 1440 px wide, in `reports/dashboard_v1/`.

| Screen | Route | Operational question | Screenshot |
|---|---|---|---|
| Overview | `/` | How did this vehicle operate in the window; how much engine-on time and fuel was stationary? | `01_overview.png` |
| Idle Intelligence | `/idle` | Are stationary periods short stops or long stands? Which merit review? Which sessions carry them? | `02_idle_intelligence.png` |
| Event Explorer | `/idle/events` | Find events by duration, fuel, session, tags or neighbouring state | `03_event_explorer.png` |
| Event Detail | `/idle/events/:id` (`?evidence=1` opens the evidence) | What was measured, how confident the detection is, and what the vehicle did before, during and after | `04_event_detail.png`, `09_evidence.png` |
| Session Explorer | `/sessions` (`?scope=all`) | The 17 engine-operation sessions (or all 46) side by side | `05_sessions.png` |
| Session Detail | `/sessions/:id` | When did the vehicle drive and when did it stand with the engine running? | `06_session_detail.png` |
| Impact Scenario | `/idle/scenario` | What would an operator-assumed share of observed stationary fuel represent? | `07_scenario.png` |
| FleetPulse AI Analyst | `/ai` | Placeholder: no LLM; explains the evidence object | `08_ai_analyst.png` |

Future modules appear in the navigation as disabled items labelled "Later": Fuel Intelligence, Engine Intelligence, Operations and Events. "Ask FleetPulse AI" is a disabled button labelled "Coming next".

**Traceability chain:**
- Overview KPI → Session Explorer / Idle Intelligence → Session Detail (timeline, events) → Event Detail → View Evidence (evidence object with rule, confidence, limitations and allowed/prohibited claims).
- Every page shows the Gold build ID and rule version in the footer.
- Event Detail also shows the Silver build ID and the session's source files.

**Charts** (each has one y-axis and hover tooltips; the series colours were validated for the dark surface):

| Chart | Question | Encoding |
|---|---|---|
| Engine-on time composition | How much of engine-on time was stationary? | single stacked bar: moving (blue `#3987e5`) and stationary engine-on (orange `#d95926`); remainder is speed-unknown |
| Stationary fuel share | What share of observed fuel was used while stationary? | share bar using Gold `stationary_fuel_pct` |
| Event length distribution | Short stops or long stands? | bars by duration class; routine in grey, extended in orange; direct count labels |
| Stationary vs moving by session | Which sessions carried the most stationary time? | horizontal stacked bars per engine-operation session; click opens the session |
| Operating timeline (small multiples) | What did the vehicle do around the event / during the session? | wheel speed, engine speed, fuel rate and coolant, each on its own axis with a synchronised crosshair; Gold events shaded |

## 5. Gold-to-UI mapping

| UI element | API field | Gold source |
|---|---|---|
| Engine-on 21.98 h | `overview.kpis.engine_on_hours` | `gold_operating_summary.engine_on_hours` |
| Distance 604.7 km | `kpis.distance_km` | `gold_operating_summary.distance_km` |
| Observed fuel 196.7 L | `kpis.fuel_consumed_l` | `gold_operating_summary.fuel_consumed_l` |
| Moving 13.64 h / stationary 8.33 h | `kpis.moving_hours` / `stationary_engine_on_hours` | same table |
| Stationary share 37.9 % | `kpis.stationary_pct_of_engine_on_time` | same table |
| Stationary fuel 19.60 L (9.96 %) | `kpis.stationary_fuel_consumed_l` / `stationary_fuel_pct` | same table |
| 17 sessions | `sessions.engine_operation_sessions` | count of `gold_session_summary` rows with `engine_on_duration_s ≥ 360` |
| 11 / 17 | `sessions.sessions_with_over_15_min_stationary` / `engine_operation_sessions` | `gold_operating_summary` / as above |
| 880 events; 778 / 86 / 13 / 3 | `idle/summary.duration_distribution` | row counts of `gold_idle_events.duration_class` |
| Routine 864 / Extended 16 | `idle/summary.attention_groups` | sums of the duration classes |
| Event table columns | `idle/events.items[]` | `gold_idle_events` columns as stored |
| HIGH CONFIDENCE / CONTEXT UNKNOWN | `event.detection_confidence` / `interpretation_status` | `gold_idle_events` |
| Session table | `sessions.items[]` | `gold_session_summary` columns as stored |
| Timeline traces | `timeline.points[]` | `silver_telemetry` (sampled rows) |
| Evidence drawer | `ai/evidence.evidence` | `gold_idle_evidence/evidence.jsonl` or `gold.evidence.evidence_object` |
| Scenario figures | `idle/scenario` response | `gold.scenario.idle_scenario` on the Gold observed fuel |

The frontend does only unit display (seconds → h or "19m 29s") and bar geometry (width = hours ÷ engine-on hours).

## 6. Wording rules

- The UI and API never describe observed fuel as waste, never call idling avoidable as a fact, and never claim savings, driver fault or the cause of a stop.
- The only place an "avoidable" percentage appears is the operator-input field on the Impact Scenario page. It sits under a banner reading **SCENARIO / ESTIMATE — NOT OBSERVED SAVINGS**, and the scenario result repeats the banner.
- The evidence drawer lists `prohibited_claims` by key (e.g. `fuel_waste`) in order to forbid them.
- `tests/test_api.py` checks every generated display string against `serving.PROHIBITED_TERMS`. The checked payloads are overview, summary, meta, event detail and evidence. The `prohibited_claims` key lists and identifiers are excluded.

## 7. Tests

`tests/test_api.py` has 52 tests:
- **Contract values:** exact Gold KPIs, 17 / 11 / 46 / 18 sessions, the 880-event distribution and attention groups, and the three highlighted events.
- **Pagination:** 9 pages × 100 events are complete and disjoint, and an empty page is returned past the end.
- **Sorting.**
- **Filters:** 8 parameterised filters; filter counts reconcile with Gold (cold engine true + false = 880).
- **Invalid parameters → 422:** page 0, oversize page, unknown sort, class or group, and negative fuel.
- **Event detail:** the reference event (19m 29s, 0.626 L, 600.1 rpm, coolant 60 → 63 °C, engine off → driving, HIGH / CONTEXT UNKNOWN, exact statements) and its timeline window.
- **Unknown IDs → 404** on events, evidence and sessions.
- **Evidence:** stored vs generated provenance, with the same key set.
- **Session drill-down:** event counts match the Gold summary, and the per-session event sum is 880.
- **Scenario:**
  - it uses the Gold module, with values equal to the observed value × pct × price;
  - 10 invalid bodies are rejected with 422, including missing inputs (no defaults), out-of-range values, `events` basis without IDs, and an empty extrapolation note;
  - the 0 % and 100 % bounds;
  - the selected-events basis, and unknown event IDs → 404;
  - the extrapolation label.
- **Wording:** prohibited terminology in fixed statements, a regex self-test, and generated payloads.

The tests run against the Part 3 Gold build and are skipped if it is absent.

## 8. Limitations

- **Data scope.** The data covers one vehicle and one archive (Part 3). The timezone is unknown, and timestamps are shown as logger wall clock, so there is no day/shift grouping. Recording sessions are not trips.
- **Interpretation.** Interpretation status is `context_unknown` for every event, because there is no GPS, job, loading, PTO or cab-heating context. The dashboard therefore ranks nothing as a problem.
- **Evidence.** 20 evidence objects are stored in Gold; the rest are generated on request by the same Gold function, and the `provenance` field marks which.
- **Timelines.** They are display-sampled (every k-th row). Short spikes between samples may not appear. Exact values are in the event row.
- **No authentication, no multi-vehicle filters, no export.** The API keeps Gold in process memory, which is suitable for this dataset size but not for fleet scale.
- **Screenshots** were captured by headless Chrome and have no interaction states. The scenario screenshot shows the empty-input state, because the result appears only after the operator enters values.
- **The AI Analyst** was added after V1; see [ai_analyst_v1.md](ai_analyst_v1.md). The "Ask FleetPulse AI" buttons are now active.
