# FleetPulse Dashboard Contract v1

This contract lists the exact datasets and fields a future dashboard may use. **The frontend holds no business logic.** Every metric, threshold, classification and label is computed by the Gold build (`fleetpulse.pipelines.gold_build`) and read as-is.

The frontend may do only these things:
- format values (units, rounding, localisation);
- sort, filter and paginate the provided rows;
- draw charts from the provided columns;
- call the scenario calculator (`fleetpulse.gold.scenario.idle_scenario`) and display its output with its label.

The frontend must never do these things:
- recompute durations, fuel, distance or percentages;
- re-derive idle events or states from telemetry;
- aggregate by calendar day (the source timezone is unknown);
- call stationary fuel "waste" or "savings".

**Sources:**
- Gold root `data/gold_part3/gold/` with tables `gold_operating_summary`, `gold_session_summary` and `gold_idle_events`, plus `gold_idle_evidence/evidence.jsonl` and `_builds/<gold_build_id>.json`.
- Silver telemetry, used only by the Session Explorer's charts.

## Conventions (all screens)

- **Timestamps** are logger wall-clock (`TIMESTAMP_NTZ`) with **timezone unknown**. Show them with the label "logger time (timezone unknown)". Never convert them to a local timezone.
- **Fuel** is litres of **observed** consumption (`fuel_method` states how).
- **Distance** is km. **Durations** are stored in seconds; display in minutes or hours is formatting only.
- **Build footer.** Every screen shows `gold_build_id`, `rule_version`, and the build time from the manifest.
- **Forbidden wording:** "waste", "wasted", "savings", "avoidable" (except inside the scenario panel, with its label), "driver fault".

## 1. Fleet Overview

Source: `gold_operating_summary` (one row, `period_type = 'dataset_total'`).

| UI element | Field(s) |
|---|---|
| Scope / period banner | `scope`, `period_label`, `period_start`, `period_end`, `timestamp_timezone` |
| KPI: engine-on hours | `engine_on_hours` |
| KPI: moving hours | `moving_hours` |
| KPI: stationary engine-on hours | `stationary_engine_on_hours` |
| KPI: stationary share of engine-on time | `stationary_pct_of_engine_on_time` |
| KPI: distance | `distance_km` |
| KPI: observed fuel | `fuel_consumed_l` |
| KPI: stationary fuel | `stationary_fuel_consumed_l`, `stationary_fuel_pct` |
| KPI: sessions | `recording_sessions`, `sessions_with_engine_on` |
| KPI: idle events | `stationary_events`, `stationary_events_over_15_min` |
| KPI: sessions with > 15 min stationary | `sessions_with_over_15_min_stationary` |

There are no per-day or per-week rows by design. Periods other than `dataset_total` will be added only once a business timezone is established.

## 2. Idle Intelligence

**Sources:** `gold_operating_summary` for headline KPIs (as in Fleet Overview); `gold_session_summary` for the chart and table; `gold_idle_events` for the duration distribution.

| UI element | Field(s) |
|---|---|
| Stationary vs moving time per session (stacked bar) | `recording_session_id`, `session_start`, `moving_duration_s`, `stationary_engine_on_duration_s`, `engine_on_speed_unknown_s` |
| Stationary share per session | `stationary_pct_of_engine_on_time` |
| Fuel split per session | `fuel_while_moving_l`, `fuel_while_stationary_l`, `fuel_while_speed_unknown_l` |
| Event-duration distribution | `gold_idle_events.duration_class` (pre-computed classes: `under_1_min`, `1_to_5_min`, `5_to_15_min`, `over_15_min`) |
| Rule disclosure panel | `_builds/<id>.json → config` (engine-on rpm, stationary speed, minimum event, rule_version) |
| Interpretation notice (fixed text) | "Stationary engine-on time is measured, not judged: the reason (loading, queue, traffic, warm-up, break, …) is unknown." |

## 3. Session Explorer

**Sources:** `gold_session_summary` for the list and header; `silver_telemetry` for charts, restricted to the selected `recording_session_id`, read-only; `gold_idle_events` to overlay event spans.

| UI element | Field(s) |
|---|---|
| Session list | `recording_session_id`, `session_start`, `session_end`, `session_duration_s`, `distance_km`, `fuel_consumed_l`, `stationary_engine_on_duration_s`, `stationary_events`, `longest_stationary_event_s` |
| Data-quality badges | `fuel_coverage_pct`, `engine_on_speed_unknown_s`, `engine_state_unknown_s` |
| Counter reconciliation badge | `fuel_consumed_l` vs `fuel_counter_l`; `distance_km` vs `distance_counter_km`. The difference is shown, not recomputed; both values come from Gold. |
| Timeline charts | `silver_telemetry.grid_timestamp`, `wheel_speed_kph`, `engine_speed_rpm`, `fuel_rate_lph`, `coolant_temp_c`; nulls are drawn as gaps, not zero |
| Idle-event overlay | `gold_idle_events.event_start`, `event_end`, `event_id` |

## 4. Idle Event Explorer

Source: `gold_idle_events`.

| UI element | Field(s) |
|---|---|
| Event table | `event_id`, `recording_session_id`, `event_start`, `event_end`, `duration_seconds`, `duration_class`, `fuel_consumed_l`, `average_fuel_rate_lph`, `average_engine_rpm`, `coolant_start_c`, `preceded_by`, `followed_by`, `descriptive_tags`, `detection_confidence`, `interpretation_status` |
| Event detail | all of the above plus `median_fuel_rate_lph`, `maximum_fuel_rate_lph`, `average_engine_load_pct`, `coolant_end_c`, `fuel_start_l`, `fuel_end_l`, `fuel_counter_delta_l`, `fuel_method`, `fuel_coverage_pct` |
| Filters | `duration_class`, `descriptive_tags`, `detection_confidence`, `recording_session_id` |
| Lineage | `rule_version`, `session_first_source_file`, `session_last_source_file`, `silver_build_id`, `gold_build_id` |

`descriptive_tags` are measured facts, not causes. The possible values are `cold_engine_at_start`, `raised_engine_speed`, `over_15_min` and `at_session_start`.

## 5. Impact Scenario

The inputs are operator-entered, and the calculation is done by `fleetpulse.gold.scenario.idle_scenario` (not the frontend).

| Input | Source |
|---|---|
| `observed_stationary_fuel_l` | `gold_operating_summary.stationary_fuel_consumed_l`, or the sum of `fuel_consumed_l` of selected events (a Gold query) |
| `operator_selected_avoidable_percentage` | operator input, 0–100; no default is pre-filled |
| `operator_selected_fuel_price` + currency | operator input |
| optional `Extrapolation(observed_period_days, projection_days, assumption_note)` | operator input; the note is required |

| Output | Display rule |
|---|---|
| `scenario_avoidable_fuel_l`, `scenario_cost_exposure` | always shown next to `label` ("SCENARIO / ESTIMATE - not observed savings") |
| `projection.*` | shown only if an extrapolation was supplied, with its `assumption_note` |
| `observed_values` / `assumed_values` | shown so the user sees which numbers are measured and which are assumed |

## 6. AI Fleet Analyst (future; no LLM in V1)

Source: `gold_idle_evidence/evidence.jsonl`, one evidence object per important event (duration ≥ 5 min, or top 10 by fuel).

| Field | Use |
|---|---|
| `evidence_id` | must be cited in every generated sentence |
| `measured.*`, `time_range`, `rule` | the only numbers the analyst may state |
| `confidence.detection`, `confidence.interpretation` | must be stated when describing the event |
| `limitations` | must be surfaced |
| `allowed_claims` / `prohibited_claims` | hard constraints for the generator and for output validation |
| `lineage` | a link back to Idle Event Explorer / Session Explorer |

**The analyst never computes vehicle metrics.** If it needs a number that is not in an evidence object or a Gold table, the answer is "not available".
