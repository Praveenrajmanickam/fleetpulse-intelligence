# Gold V1: FleetPulse Idle Intelligence

The first deterministic Gold solution. It measures **engine-on stationary operation** from validated Silver telemetry.

It **does not** judge that operation. FleetPulse cannot tell whether a stop was loading, a queue, traffic, warm-up, a break or avoidable idling, so every event carries `interpretation_status = context_unknown`, and no output uses "waste" or "savings".

```
python -m fleetpulse.pipelines.gold_build --silver-root data/gold_part3/silver --gold-root data/gold_part3/gold
```

**Code:**
- `src/fleetpulse/gold/`: `config.py` (rule), `idle.py` (Spark transformations), `scenario.py`, `evidence.py`, `storage.py`
- `src/fleetpulse/pipelines/gold_build.py`
- tests: `tests/test_gold.py`

## Scope of the Part 3 run

- **Included:** all **17 recording sessions in which the engine ran** (archive screen: ≥ 0.1 h of EEC1 ≥ 300 rpm). That is 1,361 archive members and **68,051,361 frames**, run through the unchanged Bronze → Silver pipeline (Parquet, `local[2]`, low priority). Bronze reused the 258 discovery files via its checksum ledger (skipped as unchanged) and ingested 55,151,103 new frames. Members are listed in `config/gold_part3_engine_sessions.txt`.
- **Excluded:** the remaining 1,439 members. According to the archive screen they contain **0 engine-on seconds, 0 movement, 0 fuel and 0 km**, including the 23.4 h engine-off recording of Feb 10–11. They cannot contain engine-on stationary operation.
- **Silver session count.** Built on this set, Silver has 46 recording sessions: the 17 engine sessions plus 29 short edge fragments (bus-wake bursts). 18 sessions have any engine-on time.

## 1. Definition (rule `idle-v1`)

Only verified Silver signals are used, and only when **fresh and valid**. Each 100 ms telemetry row is classified as:

| Row state | Condition |
|---|---|
| `engine_state_unknown` | EEC1 not fresh, or `engine_speed_rpm_status` ≠ valid |
| `engine_off` | engine speed fresh and valid, < **300 rpm** |
| `engine_on_speed_unknown` | engine on; CCVS not fresh or `wheel_speed_kph_status` ≠ valid |
| **`engine_on_stationary`** | engine speed ≥ 300 rpm **and** wheel-based speed < **0.5 km/h**, both fresh and valid |
| `engine_on_moving` | engine speed ≥ 300 rpm and speed ≥ 0.5 km/h, both fresh and valid |

Freshness is the Silver contract: a signal is fresh when its latest frame is at most 3 × its FMS repetition period old (EEC1 60 ms, CCVS 300 ms, LFE 300 ms).

**Development defaults.** These are configurable (`IdleRuleConfig`, CLI flags) and are *not* industry standards.

| Parameter | Default | Why |
|---|---|---|
| `engine_on_min_rpm` | 300 | Observed in part_3: running idle 566–640 rpm, cranking 130–220 rpm, off 0 rpm |
| `stationary_max_kph` | 0.5 | Stopped reads exactly 0.0 km/h (1/256 km/h resolution); creeping is about 1–2.5 km/h; same value as Stage 2 and Discovery, so results reconcile |
| `min_event_s` | 2.0 | The Discovery V1 episode minimum, and 20 grid points so a single-sample blip never forms an event |
| `long_event_s` | 900 | The "> 15 min" KPI from the business question |
| `cold_coolant_c` / `raised_idle_rpm` | 60 °C / 700 rpm | Descriptive tags only, from the Discovery observations (warm idle 600 rpm; warm-up raised idle about 900 rpm) |

## 2. Event continuity: what breaks an event

An idle event is a maximal run of consecutive `engine_on_stationary` rows (100 ms apart) of **one recording session**, lasting at least `min_event_s`.

**It ends at:**
- any **moving** row;
- an **engine-off** row;
- **any unknown row** (stale, invalid or missing engine speed or speed);
- a **hole in the grid**;
- the **session boundary**.

**Unknown rows are never bridged and never counted as idle.** A single stale speed sample therefore splits an event into two; this is tested. It never happened on Part 3: only 16 s of `engine_on_speed_unknown` and 221 s of `engine_state_unknown` exist in 22 h.

**Missing fuel does not break an event.** It lowers `fuel_coverage_pct`, and no fuel is invented for the missing rows.

**Shorter runs.** Stationary runs shorter than `min_event_s` are not emitted as events, but they **do** count in session stationary time. That is why event time (8.24 h) is slightly below stationary time (8.33 h).

## 3. Tables

**`gold_idle_events`** (one row per event)
- **Identity and timing:** `event_id` (deterministic: session + start time), `vehicle_id`, `recording_session_id`, `event_start`, `event_end` (grid timestamps of the first and last row), `duration_seconds` (rows × 0.1 s), `duration_class`.
- **Fuel counter** (HRLFC SPN 5054, informational, 0.01 L steps): `fuel_start_l`, `fuel_end_l`, `fuel_counter_delta_l`.
- **Measured fuel:** `fuel_consumed_l` (LFE SPN 183 integrated), `fuel_method`, `fuel_coverage_pct`.
- **Rates:** `average_fuel_rate_lph`, `median_fuel_rate_lph`, `maximum_fuel_rate_lph`.
- **Engine and coolant:** `average_engine_rpm`, `average_engine_load_pct`, `coolant_start_c`, `coolant_end_c`.
- **Neighbours:** `preceded_by` / `followed_by` (`driving`, `engine_off`, `signal_unavailable`, `session_start`, `session_end`), `preceded_by_driving`, `followed_by_driving`.
- **Classification:** `descriptive_tags` (measured facts: `cold_engine_at_start`, `raised_engine_speed`, `over_15_min`, `at_session_start`).
- **Confidence:**
  - `detection_confidence`: `high` at 100 % fuel coverage, `medium` at ≥ 90 %, `low` otherwise. Engine speed and speed are fresh and valid on every row by definition.
  - `interpretation_status`: always `context_unknown`.
- **Lineage:** `rule_version`, `session_first_source_file`, `session_last_source_file`, `silver_build_id`, `gold_build_id`.

**`gold_session_summary`** (one row per recording session)
- engine-on, moving and stationary engine-on durations, plus unknown and engine-off durations;
- distance (integrated) and the distance counter;
- observed fuel (integrated) and the fuel counter;
- fuel while moving / stationary / speed unknown;
- stationary % of engine-on time;
- number of events, event time, longest event, events over 15 min;
- fuel coverage and lineage.

**`gold_operating_summary`**
- One row: `period_type = 'dataset_total'`, `timestamp_timezone = 'unknown (logger wall clock)'`, with the explicit `scope` text.
- **There are no per-day rows by design**: `event_date` is the logger's calendar date in an unknown timezone, not a business day.

**`gold_idle_evidence/evidence.jsonl`**: evidence objects for events ≥ 5 min plus the top 10 by fuel (20 objects on Part 3).

**Storage:** a full deterministic rebuild from Silver, staged and then published by rename, with a build manifest (`_builds/<id>.json`: config, counts, per-session counter validation). The same semantics as Silver Parquet.

## 4. Measured Part 3 results (gold build `20260930T071045Z-0ae17fad`)

| Metric | Value |
|---|---|
| Engine-on hours | **21.98 h** |
| Moving hours | **13.64 h** |
| Stationary engine-on hours | **8.33 h = 37.9 % of engine-on time** |
| Distance | **604.7 km** |
| Observed fuel | **196.7 L** |
| Stationary (engine-on) fuel | **19.60 L = 9.96 %** |
| Idle events (≥ 2 s) | **880**: 778 under 1 min, 86 1–5 min, 13 5–15 min, 3 over 15 min |
| Event time / event fuel | 8.24 h / 19.32 L |
| Sessions with > 15 min stationary (total per session) | **11** of 17 engine sessions |
| Detection confidence | 880 / 880 `high` (100 % fuel coverage) |
| Event neighbours | 862 preceded by driving, 17 by engine off, 1 by signal unavailable |
| Tags | 45 `cold_engine_at_start`, 13 `raised_engine_speed`, 3 `over_15_min` |

**Longest events:**

| Start | Duration | Fuel | Mean rate | Mean rpm | Coolant (start→end) | Context |
|---|---|---|---|---|---|---|
| Jan 19 09:24:02.9 | 19.5 min | 0.63 L | 1.93 L/h | 600 | 60 → 63 °C | right after engine start, then driving |
| Feb 10 08:46:08.3 | 19.3 min | 0.74 L | 2.31 L/h | 600 | 69 → 66 °C | between driving |
| Feb 04 11:43:23.7 | 18.9 min | 0.56 L | 1.76 L/h | 624 | 76 → 72 °C | between driving |
| Feb 10 10:19:08.5 | 12.4 min | 0.40 L | 1.92 L/h | 688 | 69 → 67 °C | between driving |
| Jan 19 08:51:03.6 | 11.7 min | 0.36 L | 1.83 L/h | 600 | 68 → 67 °C | ends when the signal becomes unavailable |
| Jan 21 08:22:41.3 | 9.4 min | 0.45 L | 2.88 L/h | 640 | 38 → 47 °C | cold engine at start |

**Operating profiles** (from `gold_session_summary`):
- **Road sessions** (Jan 18 15:32, Jan 19 11:51, Feb 4 ×3, Feb 10 09:41): 5.5–28 % stationary.
- **Low-mobility sessions** (Jan 19 ×3, Jan 21 ×4, Feb 10 10:10): 50–76 % stationary with ≤ 6.5 km each.
- **Mixed:** Jan 18 12:35 (29 %, 98 km), Feb 2 (19 %, 28 km), Feb 10 08:01 (50 %, 32 km).

## 5. Validation and reconciliation

| Check | Result |
|---|---|
| Fuel: integrated vs truck fuel counter (HRLFC SPN 5054), 17 sessions > 1 L | 196.62 vs 197.02 L (−0.20 %); worst session 0.069 L |
| Distance: integrated vs vehicle distance counter (VDHR SPN 917) | 603.83 vs 604.68 km; worst session 0.195 km (wheel speed under-reads at creep speed) |
| Example event counter check (Jan 19 09:24 event) | counter 0.621 L vs integrated 0.626 L |
| **vs Discovery V1: the 5 analysed sessions** | **identical**: 109 events, 43.2 min, 2.54 L |
| **vs Discovery V1: archive screen** | engine-on 21.98 vs 21.96 h; moving 13.64 vs 13.65 h; stationary 8.33 vs 8.30 h; fuel 196.7 vs 196.3 L; stationary fuel 19.60 vs 19.56 L; km 604.7 vs 603.7; sessions > 15 min 11 vs 11 |

The small differences from the screen come from method:
- **Alignment.** The screen matched fuel and engine samples to the nearest speed sample (300 ms tolerance, per file). Gold uses Silver's 100 ms grid with freshness gating: rows with a stale or invalid signal (about 4 min over 22 h) are excluded rather than attributed.
- **Frame hold time.** The screen capped how long each frame counted, at 60 ms (EEC1) and 300 ms (CCVS/LFE). Gold uses one 100 ms grid row per sample.
- **Rounding.** The integration boundaries differ slightly at file edges.

## 6. Impact scenario (`gold/scenario.py`)

`idle_scenario(observed_stationary_fuel_l, avoidable_pct, fuel_price_per_l, currency, extrapolation=None)` returns `scenario_avoidable_fuel_l` and `scenario_cost_exposure`.
- **Labels:** every output is labelled **"SCENARIO / ESTIMATE - not observed savings"**, and lists which values are observed and which are assumed.
- **Input checks:** avoidable % must be 0–100; negative fuel or price is rejected.
- **Projections** are produced only from an explicit `Extrapolation(observed_period_days, projection_days, assumption_note)`, and the note is mandatory.

Example: 19.60 L × 25 % (operator assumption) × €1.80 → 4.90 L and €8.82, *scenario only*.

## 7. AI evidence object

The Jan 19 09:24 event, abridged. The full object is in `evidence.jsonl`.

```json
{
  "evidence_id": "turku-renault-t520-6x2-20210119T092357960305-idle-092402900000",
  "event_type": "engine_on_stationary",
  "rule": {"version": "idle-v1", "definition": "engine_speed_rpm >= 300 AND wheel_speed_kph < 0.5, both fresh and valid, contiguous within one recording session, >= 2 s; broken by movement, engine off, unknown signals or session end", "thresholds_are_development_defaults": true},
  "time_range": {"start": "2021-01-19 09:24:02.900000", "end": "2021-01-19 09:43:32", "timezone": "unknown (logger wall clock)"},
  "measured": {"duration_seconds": 1169.2, "fuel_consumed_l": 0.626, "average_fuel_rate_lph": 1.926, "median_fuel_rate_lph": 1.7,
               "maximum_fuel_rate_lph": 12.55, "average_engine_rpm": 600.1, "average_engine_load_pct": 10.0,
               "coolant_start_c": 60.0, "coolant_end_c": 63.0, "fuel_counter_start_l": 2604.879, "fuel_counter_end_l": 2605.5,
               "preceded_by": "engine_off", "followed_by": "driving", "descriptive_tags": ["over_15_min"]},
  "supporting_signals": ["EEC1 SPN 190 (fresh+valid every row)", "CCVS SPN 84 (fresh+valid every row)", "LFE SPN 183 (100 % coverage)", "ET1 SPN 110 (context)"],
  "confidence": {"detection": "high", "interpretation": "context_unknown"},
  "limitations": ["no GPS/location, job or loading context: the reason for the stop is unknown", "..."],
  "allowed_claims": ["the engine was running while the vehicle was stationary for the stated duration", "..."],
  "prohibited_claims": ["fuel_waste", "avoidable_idling", "cost_savings", "driver_fault", "cause_of_stop", "location_or_activity", "comparison_to_other_vehicles_or_fleets"],
  "lineage": {"gold_build_id": "20260930T071045Z-0ae17fad", "silver_build_id": "20260930T070433Z-114b9d90", "event_id": "..."}
}
```

## 8. Tests (`tests/test_gold.py`, 18 tests)

- event between driving (duration, boundaries, fuel, neighbours, session split);
- engine-off transition;
- stale speed never bridged;
- missing or invalid speed value;
- unknown engine state;
- missing fuel (coverage, confidence, no invented fuel);
- session boundaries;
- grid hole;
- short run (not an event, but counted in session time);
- a long event over 15 min with tags;
- counters and operating summary;
- scenario: labels and arithmetic, invalid inputs, projection only with an explicit note;
- evidence-object selection and content.

## 9. Limitations

- **One vehicle, Part 3 only (about 3 weeks).** No fleet comparison is possible.
- **No context:** no location, job, loading, driver, PTO/heater or idle-increment state. The **cause** of any stationary period is unknown. 862 of the 880 events sit between driving periods and are mostly under 1 min, which is consistent with traffic and stop-and-go but *not provable*.
- **Timezone unknown.** There are no time-of-day or per-day KPIs.
- **Fuel** is an FMS indication, reconciled with the counter within 0.2 %.
- **Development defaults.** The thresholds should be revisited with operator input and other vehicles or loggers.
