// Typed client for the FleetPulse API v1. The UI only formats these values; it never derives metrics.

export interface BuildInfo {
  gold_build_id: string;
  built_at_utc: string;
  rule_version: string;
  silver_build_ids: string[];
  rule_config: Record<string, unknown>;
}

export interface Scope {
  vehicles: number;
  vehicle_id: string;
  observation_window: string;
  statement: string;
  period_start: string;
  period_end: string;
  timestamp_timezone: string;
  gold_scope: string;
}

export interface Module {
  id: string;
  label: string;
  available: boolean;
}

export interface Meta {
  build: BuildInfo;
  scope: Scope;
  module_statement: string;
  modules: Module[];
}

export interface Overview {
  scope: Scope;
  kpis: {
    engine_on_hours: number;
    moving_hours: number;
    stationary_engine_on_hours: number;
    stationary_pct_of_engine_on_time: number;
    distance_km: number;
    fuel_consumed_l: number;
    stationary_fuel_consumed_l: number;
    stationary_fuel_pct: number;
    stationary_events: number;
    stationary_events_over_15_min: number;
    sessions_with_over_15_min_stationary: number;
  };
  sessions: {
    engine_operation_sessions: number;
    engine_operation_definition: string;
    sessions_with_over_15_min_stationary: number;
    recording_sessions_total: number;
    sessions_with_any_engine_on: number;
    short_engine_fragments: {
      recording_session_id: string;
      session_start: string;
      engine_on_duration_s: number;
      stationary_events: number;
      fuel_consumed_l: number;
    }[];
    note: string;
  };
  engine_time_composition_hours: { state: string; hours: number }[];
  module_statement: string;
  build: BuildInfo;
}

export interface IdleEvent {
  event_id: string;
  recording_session_id: string;
  event_start: string;
  event_end: string;
  duration_seconds: number;
  duration_class: string;
  fuel_consumed_l: number | null;
  average_fuel_rate_lph: number | null;
  median_fuel_rate_lph: number | null;
  maximum_fuel_rate_lph: number | null;
  average_engine_rpm: number | null;
  average_engine_load_pct: number | null;
  coolant_start_c: number | null;
  coolant_end_c: number | null;
  preceded_by: string;
  followed_by: string;
  descriptive_tags: string[];
  detection_confidence: string;
  interpretation_status: string;
  attention_group: "routine_short" | "extended";
  evidence_stored: boolean;
  [extra: string]: unknown;
}

export interface SessionRow {
  recording_session_id: string;
  session_start: string;
  session_end: string;
  session_duration_s: number;
  engine_on_duration_s: number;
  moving_duration_s: number;
  stationary_engine_on_duration_s: number;
  engine_on_speed_unknown_s: number;
  distance_km: number | null;
  distance_counter_km: number | null;
  fuel_consumed_l: number | null;
  fuel_counter_l: number | null;
  fuel_while_moving_l: number | null;
  fuel_while_stationary_l: number | null;
  stationary_pct_of_engine_on_time: number | null;
  stationary_events: number;
  longest_stationary_event_s: number | null;
  stationary_events_over_15_min: number;
  fuel_coverage_pct: number | null;
}

export interface AttentionGroup {
  label: string;
  duration_classes: string[];
  description: string;
  events: number;
}

export interface IdleSummary {
  scope: Scope;
  events_total: number;
  stationary_engine_on_hours: number;
  stationary_fuel_consumed_l: number;
  duration_distribution: { duration_class: string; label: string; events: number; attention_group: string }[];
  attention_groups: Record<"routine_short" | "extended", AttentionGroup>;
  extended_events: IdleEvent[];
  highlighted_events: IdleEvent[];
  sessions: Pick<SessionRow, "recording_session_id" | "session_start" | "engine_on_duration_s" | "moving_duration_s" |
    "stationary_engine_on_duration_s" | "stationary_pct_of_engine_on_time" | "stationary_events" |
    "longest_stationary_event_s" | "fuel_while_stationary_l" | "distance_km">[];
  module_statement: string;
  rule: Record<string, unknown>;
  build: BuildInfo;
}

export interface EventPage {
  total: number;
  page: number;
  page_size: number;
  pages: number;
  sort: string;
  items: IdleEvent[];
  filter_options: { duration_class: string[]; preceded_by: string[]; followed_by: string[] };
}

export interface TimelinePoint {
  t: string;
  wheel_speed_kph: number | null;
  engine_speed_rpm: number | null;
  fuel_rate_lph: number | null;
  coolant_temp_c: number | null;
  engine_load_pct: number | null;
}

export interface Timeline {
  available: boolean;
  source?: string;
  sampling_step_rows?: number;
  sampling_note?: string;
  points: TimelinePoint[];
  window_start?: string;
  window_end?: string;
  event_start?: string;
  event_end?: string;
}

export interface EventDetail {
  event: IdleEvent;
  interpretation: {
    detection_confidence: string;
    detection_statement: string;
    interpretation_status: string;
    interpretation_statement: string;
  };
  session: { recording_session_id: string; session_start: string; session_end: string };
  timeline: Timeline;
  scope: Scope;
  build: BuildInfo;
}

export interface SessionDetail {
  session: SessionRow;
  events: IdleEvent[];
  timeline: Timeline;
  scope: Scope;
  build: BuildInfo;
}

export interface Evidence {
  provenance: string;
  evidence: {
    evidence_id: string;
    event_type: string;
    rule: { version: string; definition: string; thresholds_are_development_defaults: boolean };
    time_range: { start: string; end: string; timezone: string };
    measured: Record<string, unknown>;
    supporting_signals: Record<string, unknown>[];
    confidence: { detection: string; detection_basis: string; interpretation: string; interpretation_basis: string };
    limitations: string[];
    allowed_claims: string[];
    prohibited_claims: string[];
    [extra: string]: unknown;
  };
}

export interface ScenarioRequest {
  avoidable_pct: number;
  fuel_price_per_l: number;
  currency: string;
  basis: "all_stationary" | "events";
  event_ids?: string[];
  extrapolation?: { observed_period_days: number; projection_days: number; assumption_note: string };
}

export interface ScenarioResult {
  label: string;
  inputs: {
    observed_stationary_fuel_l: number;
    operator_selected_avoidable_percentage: number;
    operator_selected_fuel_price_per_l: number;
    currency: string;
  };
  scenario_avoidable_fuel_l: number;
  scenario_cost_exposure: number;
  observed_values: string[];
  assumed_values: string[];
  projection: null | {
    label: string;
    observed_period_days: number;
    projection_days: number;
    assumption_note: string;
    scale_factor: number;
    projected_avoidable_fuel_l: number;
    projected_cost_exposure: number;
  };
  basis: { basis: string; description: string; event_ids?: string[] };
  calculated_by: string;
  build: BuildInfo;
}

/** How the UI should treat a failed request. "unavailable" = the backend could not be reached. */
export type ApiErrorKind = "unavailable" | "not_found" | "invalid" | "server";

export class ApiError extends Error {
  constructor(public status: number, message: string, public kind: ApiErrorKind = classifyStatus(status, true)) {
    super(message);
  }
}

/** FastAPI always answers with JSON. A non-JSON 5xx comes from a proxy / gateway whose upstream (the API) is down. */
export function classifyStatus(status: number, jsonBody: boolean): ApiErrorKind {
  if (status === 0 || status === 502 || status === 503 || status === 504) return "unavailable";
  if (status >= 500) return jsonBody ? "server" : "unavailable";
  if (status === 404) return "not_found";
  return "invalid";
}

export const isUnavailable = (e: unknown): boolean => e instanceof ApiError && e.kind === "unavailable";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`/api/v1${path}`, init);
  } catch {
    throw new ApiError(0, "The FleetPulse API could not be reached.", "unavailable");
  }
  if (!r.ok) {
    let detail = r.statusText;
    let jsonBody = false;
    try {
      const body = await r.json();
      jsonBody = true;
      detail = typeof body.detail === "string" ? body.detail
        : body.detail?.message ?? (Array.isArray(body.detail) ? body.detail.map((d: { msg: string }) => d.msg).join("; ") : JSON.stringify(body.detail));
    } catch {
      /* keep statusText */
    }
    throw new ApiError(r.status, detail, classifyStatus(r.status, jsonBody));
  }
  return r.json() as Promise<T>;
}

export type EventQuery = Record<string, string | number | boolean | string[] | undefined>;

function qs(params: EventQuery): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => u.append(k, x));
    else u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export const api = {
  meta: () => request<Meta>("/meta"),
  overview: () => request<Overview>("/overview"),
  idleSummary: () => request<IdleSummary>("/idle/summary"),
  events: (q: EventQuery) => request<EventPage>(`/idle/events${qs(q)}`),
  event: (id: string) => request<EventDetail>(`/idle/events/${encodeURIComponent(id)}`),
  sessions: (scope: "engine_operation" | "all") =>
    request<{ scope: string; definition: string; total: number; items: SessionRow[] }>(`/sessions?scope=${scope}`),
  session: (id: string) => request<SessionDetail>(`/sessions/${encodeURIComponent(id)}`),
  evidence: (id: string) => request<Evidence>(`/ai/evidence/${encodeURIComponent(id)}`),
  scenario: (body: ScenarioRequest) =>
    request<ScenarioResult>("/idle/scenario", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};

// ---- AI Analyst V1 (explanatory layer; all numbers come from FleetPulse) ----

export type AnalystScope = "event" | "session" | "overall" | "scenario";

export interface AiStatus {
  available: boolean;
  provider: string | null;
  model: string | null;
  is_mock: boolean;
  message: string;
  policy_version: string;
  max_question_chars: number;
}

export interface AskBody {
  question: string;
  scope: AnalystScope;
  event_id?: string;
  session_id?: string;
  scenario?: { avoidable_pct?: number; fuel_price_per_l?: number; currency?: string; event_ids?: string[] };
}

export interface MeasuredFact {
  id: string;
  label: string;
  value: unknown;
  unit: string | null;
  display: string;
  source: "gold" | "derived_from_gold" | "gold_scenario_engine";
}

export interface AskResponse {
  request_id: string;
  status: "answered" | "answered_after_correction" | "fallback_validation_failed" | "fallback_provider_error";
  answer: string;
  answer_source: "llm" | "mock" | "deterministic_fallback";
  scope: AnalystScope;
  subject_id: string | null;
  intent: string;
  evidence_ids: string[];
  measured_facts: MeasuredFact[];
  fleetpulse_interpretation: string[];
  limitations: string[];
  suggested_investigations: string[];
  confidence: { detection: string; interpretation: string };
  scenario: ScenarioResult | null;
  scenario_inputs_source: string | null;
  gold_build_id: string;
  rule_version: string;
  provider: string;
  model: string;
  is_mock: boolean;
  generated_at: string;
  validation: { passed: boolean; attempts: number; issues: string[] };
  input_flags: string[];
  evidence_context: Record<string, unknown>;
  policy_version: string;
}

export const aiApi = {
  status: () => request<AiStatus>("/ai/status"),
  ask: (body: AskBody) =>
    request<AskResponse>("/ai/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};

// ---- Signals (Dashboard V2): catalog, data/signal health, traceability ----

export type SystemId = "engine" | "fuel" | "cooling" | "driver" | "motion" | "environment";

export interface SignalCaveat {
  level: "quality_warning" | "unavailable" | "note";
  text: string;
  source: string;
}

export interface SignalCatalogItem {
  name: string;
  label: string;
  system: SystemId;
  system_label: string;
  pgn: number;
  pgn_hex: string;
  message: string;
  message_name: string;
  spn: number;
  unit: string;
  description: string;
  fms_period_ms: number;
  freshness_limit_ms: number;
  freshness_column: string;
  status_column: string;
  decode: {
    position: string;
    start_byte: number;
    length_bits: number;
    byte_order: string;
    kind: "measured" | "discrete";
    resolution?: number;
    offset?: number;
    unit?: string;
    operating_range?: [number, number];
    formula?: string;
    start_bit?: number;
    states?: Record<string, string>;
    not_available_code?: number;
    error_code?: number;
  };
  reference: string;
  standard: string;
  caveats: SignalCaveat[];
  gold_use: { kind: string; text: string };
}

export interface HealthScope {
  rows: number;
  status_counts: Record<string, number>;
  freshness_counts: Record<string, number>;
  available_rows: number;
  stale_rows: number;
  no_observation_rows: number;
  unavailable_rows: number;
  available_pct: number | null;
}

export type HealthState = "available" | "partial" | "unavailable" | "quality_warning";

export interface SignalHealthItem {
  name: string;
  label: string;
  system: SystemId;
  message: string;
  pgn: number;
  spn: number;
  state: HealthState;
  caveats: SignalCaveat[];
  scopes: { all_rows: HealthScope; engine_running_rows: HealthScope };
}

export interface SignalHealth {
  statement: string;
  scope: {
    vehicle_id: string;
    vehicles: number;
    dataset: string;
    silver_build_ids: string[];
    recording_sessions: number;
    grid: string;
    window_start: string;
    window_end: string;
    timestamp_timezone: string;
    historical: boolean;
    note: string;
  };
  rows_analysed: number;
  engine_running_rows: number;
  engine_running_definition: string;
  freshness_rule: string;
  state_rules: Record<HealthState, string>;
  signals: SignalHealthItem[];
}

export interface SignalTrace {
  signal: string;
  label: string;
  system: SystemId;
  anchor: { event_id: string; default_reference_event: boolean; rule: string };
  steps: {
    raw_can: {
      timestamp: string; can_id: number; can_id_hex: string; dlc: number; data: (number | null)[]; data_hex: string;
      source_archive: string; source_file: string; source_line: number; ingestion_run_id: string;
    };
    j1939: {
      priority: number; pgn: number; pgn_hex: string; pdu_format: number; pdu_specific: number; is_pdu2: boolean;
      source_address: number; message: string; message_name: string;
    };
    spn: { spn: number; label: string; reference: string; description: string };
    decode: SignalCatalogItem["decode"] & { bytes: { byte: number; value: number | null; hex: string | null }[]; raw: number | null };
    physical: { value: number | null; unit: string; status: string; state_label: string | null };
    silver_signal_event: null | {
      event_timestamp: string; message: string; pgn: number; spn: number; signal_name: string; signal_raw: number;
      signal_value: number | null; signal_unit: string; signal_status: string; source_file: string; source_line: number;
      ingestion_run_id: string; silver_build_id: string;
    };
    silver_telemetry: {
      grid_timestamp: string; column: string; value: number | null; status: string | null; age_ms: number;
      freshness: string; freshness_limit_ms: number; recording_session_id: string; silver_build_id: string;
    };
    gold_event: {
      event_id: string; event_start: string; event_end: string; duration_seconds: number; fuel_consumed_l: number;
      average_engine_rpm: number; detection_confidence: string; interpretation_status: string; rule_version: string;
      gold_build_id: string; signal_role: { kind: string; text: string };
    };
    intelligence: { text: string; attention_group: string };
  };
  checks: { decoder_matches_silver_event: boolean | null; decoder_matches_telemetry: boolean };
}

/** Catalog and health are static per Gold/Silver build: fetch once per page load (failed requests are not cached). */
const once = <T,>(fn: () => Promise<T>) => {
  let p: Promise<T> | undefined;
  return () => (p ??= fn().catch((e) => { p = undefined; throw e; }));
};

export const signalsApi = {
  catalog: once(() => request<{ standard: string; count: number; source: string; items: SignalCatalogItem[] }>("/signals")),
  health: once(() => request<SignalHealth>("/signals/health")),
  trace: (name: string, eventId?: string) =>
    request<SignalTrace>(`/signals/${encodeURIComponent(name)}/trace${eventId ? `?event_id=${encodeURIComponent(eventId)}` : ""}`),
};
