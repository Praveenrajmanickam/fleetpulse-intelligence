import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import VehicleVisualization from "../components/vehicle/VehicleVisualization";
import { VEHICLE_SYSTEMS, type SignalAnnotation } from "../components/vehicle/types";
import { TimelineMultiples } from "../components/charts";
import AnalystPanel from "../components/AnalystPanel";
import EvidenceDrawer from "../components/EvidenceDrawer";
import { AttentionBadge, ConfidenceBadge, ErrorBox, InterpretationBadge, Kpi, Loading, Panel, Tags } from "../components/ui";
import { NotFoundState } from "../components/foundation/ApiState";
import { ApiError, api } from "../lib/api";
import { clock, duration, neighbour, num, shortSession, ts, useApi } from "../lib/format";

/** Context that would explain a stop, and why it is absent (docs/business_discovery_v1.md, docs/signal_definitions.md, Gold evidence). */
const MISSING_CONTEXT: [string, string][] = [
  ["GPS / location & route", "not in the dataset"],
  ["Job / loading state", "no dispatch or job data"],
  ["Payload / vehicle mass", "VW PGN absent"],
  ["Road grade", "no grade or altitude"],
  ["PTO state", "CCVS SPN 976 not decoded"],
  ["Driver / tachograph context", "TCO1 present, not decoded"],
];

export default function EventDetail() {
  const { eventId = "" } = useParams();
  const { data, error, reload } = useApi(() => api.event(eventId), [eventId]);
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const [showEvidence, setShowEvidence] = useState(sp.get("evidence") === "1");
  const [showAnalyst, setShowAnalyst] = useState(sp.get("ask") === "1");
  if (error) {
    if (error instanceof ApiError && error.kind === "not_found") {
      return (
        <div className="page">
          <div className="crumbs"><Link to="/idle/events">Event Explorer</Link></div>
          <NotFoundState title="Event not found" message={`No Gold idle event with id ${eventId}.`} backTo="/idle/events" backLabel="Back to Event Explorer" />
        </div>
      );
    }
    return <ErrorBox error={error} onRetry={reload} />;
  }
  if (!data) return <Loading />;
  const e = data.event;
  const i = data.interpretation;
  const extended = e.attention_group === "extended";
  const ctx = { kind: "reference_event" as const, eventId: e.event_id, label: "This event" };
  const annotations: SignalAnnotation[] = [
    { signal: "engine_speed_rpm", label: "Avg engine speed", value: num(e.average_engine_rpm, 1), unit: "rpm", context: ctx, provenance: "measured" },
    { signal: "fuel_rate_lph", label: "Fuel observed", value: num(e.fuel_consumed_l, 3), unit: "L", context: ctx, provenance: "measured" },
    { signal: "coolant_temp_c", label: "Coolant", value: `${num(e.coolant_start_c, 0)} → ${num(e.coolant_end_c, 0)}`, unit: "°C", context: ctx, provenance: "measured" },
    { signal: "wheel_speed_kph", label: "Vehicle state", value: "Stationary", context: ctx, provenance: "derived" },
  ];
  return (
    <div className="page event-page">
      <div className="crumbs">
        <Link to="/">Overview</Link> / <Link to="/sessions">Sessions</Link> /{" "}
        <Link to={`/sessions/${encodeURIComponent(e.recording_session_id)}`}>{shortSession(e.recording_session_id)}</Link> / event
      </div>
      <div className="page-head spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow">Investigation workspace · Engine-on stationary event</div>
          <h1>{ts(e.event_start)} – {clock(e.event_end)}</h1>
          <div className="row">
            <ConfidenceBadge level={i.detection_confidence} />
            <InterpretationBadge status={i.interpretation_status} />
            <AttentionBadge group={e.attention_group} />
            <Tags tags={e.descriptive_tags} />
          </div>
        </div>
        <div className="row">
          <button className="btn primary" onClick={() => setShowEvidence(true)}>View Evidence</button>
          <button className="btn" onClick={() => setShowAnalyst(true)}>Ask FleetPulse AI</button>
        </div>
      </div>

      {/* BEFORE -> EVENT -> AFTER */}
      <ol className="sequence" aria-label="Operating sequence">
        <li className="seq-step"><span className="seq-k">Before</span><span className="seq-v">{neighbour(e.preceded_by)}</span></li>
        <li className="seq-arrow" aria-hidden="true">→</li>
        <li className={`seq-step seq-event${extended ? " extended" : ""}`}>
          <span className="seq-k">Event</span>
          <span className="seq-v">Stationary, engine on · <b className="mono">{duration(e.duration_seconds)}</b></span>
        </li>
        <li className="seq-arrow" aria-hidden="true">→</li>
        <li className="seq-step"><span className="seq-k">After</span><span className="seq-v">{neighbour(e.followed_by)}</span></li>
        <li className="seq-note small muted">Sequence from Gold neighbour states. It does not state why the vehicle stopped.</li>
      </ol>

      <div className="event-grid">
        <div className="panel event-twin">
          <div className="spread"><h2>Systems involved</h2><span className="small muted">Engine · Fuel · Cooling · Motion</span></div>
          <VehicleVisualization variant="hero" highlightedSystems={["engine", "fuel", "cooling", "motion"]} annotations={annotations}
            onSystemSelect={(s) => s && nav(`/vehicle/sensors?signal=${VEHICLE_SYSTEMS.find((x) => x.id === s)?.signals[0]}`)}
            caption="Vehicle systems measured during this event" />
        </div>
        <div className="event-facts">
          <div className="grid g2">
            <Kpi label="Duration" value={duration(e.duration_seconds)} sub={`${num(e.duration_seconds, 1)} s`} provenance="measured" />
            <Kpi label="Fuel observed" value={num(e.fuel_consumed_l, 3)} unit="L" provenance="measured"
              sub={`avg ${num(e.average_fuel_rate_lph, 2)} · max ${num(e.maximum_fuel_rate_lph, 2)} L/h`} />
            <Kpi label="Average engine speed" value={num(e.average_engine_rpm, 1)} unit="rpm" provenance="measured" sub={`load ${num(e.average_engine_load_pct, 1)} %`} />
            <Kpi label="Coolant" value={`${num(e.coolant_start_c, 0)} → ${num(e.coolant_end_c, 0)}`} unit="°C" provenance="measured" sub="start → end of event" />
          </div>
          <div className="grid g2" style={{ marginTop: 16 }}>
            <div className="verdict">
              <div className="verdict-k">Detection</div>
              <div className="verdict-v good">{i.detection_confidence.toUpperCase()}</div>
              <div className="small muted">{i.detection_statement} Fuel data on {num(e.fuel_coverage_pct as number, 1)} % of rows.</div>
            </div>
            <div className="verdict">
              <div className="verdict-k">Interpretation</div>
              <div className="verdict-v">{i.interpretation_status.replace(/_/g, " ").toUpperCase()}</div>
              <div className="small muted">{i.interpretation_statement}</div>
            </div>
          </div>
          <div className="panel missing-ctx">
            <h3>Context not available in this dataset</h3>
            <ul>
              {MISSING_CONTEXT.map(([k, v]) => <li key={k}><span aria-hidden="true">⊘</span><b>{k}</b><span className="muted"> — {v}</span></li>)}
            </ul>
            <p className="small muted" style={{ margin: "8px 0 0" }}>Without this context FleetPulse does not classify the event as waste, avoidable or caused by anything.</p>
          </div>
        </div>
      </div>

      <div className="section">
        <Panel title="Operating timeline around the event"
          question={`What was the vehicle doing before, during and after? Window ${ts(data.timeline.window_start)} – ${clock(data.timeline.window_end ?? "")}, up to 2 min either side, clipped to the recording session.`}
          provenance="measured">
          <TimelineMultiples timeline={data.timeline} spans={[{ start: e.event_start, end: e.event_end, highlight: true }]} />
        </Panel>
      </div>

      <div className="grid g-2-1 section">
        <Panel title="Evidence trail">
          <div className="row" style={{ gap: 8 }}>
            <Link className="btn small-btn" to={`/vehicle/trace?signal=engine_speed_rpm&event_id=${encodeURIComponent(e.event_id)}`}>Trace engine speed CAN → Gold</Link>
            <Link className="btn small-btn" to={`/vehicle/trace?signal=fuel_rate_lph&event_id=${encodeURIComponent(e.event_id)}`}>Trace fuel rate</Link>
            <Link className="btn small-btn" to={`/vehicle/trace?signal=wheel_speed_kph&event_id=${encodeURIComponent(e.event_id)}`}>Trace wheel speed</Link>
            <button className="btn small-btn" onClick={() => setShowEvidence(true)}>Evidence object</button>
          </div>
        </Panel>
        <Panel title="Lineage">
          <dl className="dl">
            <dt>Event</dt><dd style={{ wordBreak: "break-all" }}>{e.event_id}</dd>
            <dt>Session</dt><dd><Link to={`/sessions/${encodeURIComponent(e.recording_session_id)}`}>{shortSession(e.recording_session_id)}</Link></dd>
            <dt>Rule</dt><dd>{String(e.rule_version)}</dd>
            <dt>Gold build</dt><dd>{String(e.gold_build_id)}</dd>
            <dt>Silver build</dt><dd>{String(e.silver_build_id)}</dd>
            <dt>Source files</dt><dd style={{ wordBreak: "break-all" }}>{String(e.session_first_source_file)} … {String(e.session_last_source_file)}</dd>
          </dl>
        </Panel>
      </div>

      {showEvidence && <EvidenceDrawer eventId={e.event_id} onClose={() => setShowEvidence(false)} />}
      {showAnalyst && <AnalystPanel scope="event" eventId={e.event_id} initialQuestion={sp.get("q")} onClose={() => setShowAnalyst(false)} />}
    </div>
  );
}
