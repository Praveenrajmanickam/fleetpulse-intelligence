import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { TimelineMultiples } from "../components/charts";
import AnalystPanel from "../components/AnalystPanel";
import EvidenceDrawer from "../components/EvidenceDrawer";
import { AttentionBadge, ConfidenceBadge, InterpretationBadge, Kpi, Loading, Panel, Tags } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { clock, duration, neighbour, num, shortSession, ts, useApi } from "../lib/format";

export default function EventDetail() {
  const { eventId = "" } = useParams();
  const { data, error } = useApi(() => api.event(eventId), [eventId]);
  const [sp] = useSearchParams();
  const [showEvidence, setShowEvidence] = useState(sp.get("evidence") === "1");
  const [showAnalyst, setShowAnalyst] = useState(sp.get("ask") === "1");
  if (error) {
    return (
      <div className="page">
        <div className="crumbs"><Link to="/idle/events">Event Explorer</Link></div>
        <h1>Event not found</h1>
        <p className="lede">{error instanceof ApiError && error.status === 404 ? `No Gold idle event with id ${eventId}.` : error.message}</p>
      </div>
    );
  }
  if (!data) return <Loading />;
  const e = data.event;
  const i = data.interpretation;
  return (
    <div className="page">
      <div className="crumbs">
        <Link to="/">Overview</Link> / <Link to="/sessions">Sessions</Link> /{" "}
        <Link to={`/sessions/${encodeURIComponent(e.recording_session_id)}`}>{shortSession(e.recording_session_id)}</Link> / event
      </div>
      <div className="page-head spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow">Engine-on stationary event</div>
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

      <div className="grid g4">
        <Kpi label="Duration" value={duration(e.duration_seconds)} sub={`${num(e.duration_seconds, 1)} s`} />
        <Kpi label="Fuel observed" value={num(e.fuel_consumed_l, 3)} unit="L"
          sub={`avg ${num(e.average_fuel_rate_lph, 2)} · median ${num(e.median_fuel_rate_lph, 2)} · max ${num(e.maximum_fuel_rate_lph, 2)} L/h`} />
        <Kpi label="Average engine speed" value={num(e.average_engine_rpm, 1)} unit="rpm" sub={`load ${num(e.average_engine_load_pct, 1)} %`} />
        <Kpi label="Coolant" value={`${num(e.coolant_start_c, 0)} → ${num(e.coolant_end_c, 0)}`} unit="°C" sub="start → end of event" />
      </div>

      <div className="grid g-2-1 section">
        <Panel title="Detection & interpretation">
          <p className="statement" style={{ marginTop: 0 }}>
            <b style={{ color: "#fff" }}>{i.detection_statement}</b> {i.interpretation_statement}
          </p>
          <dl className="dl">
            <dt>Detection</dt><dd>{i.detection_confidence.toUpperCase()} CONFIDENCE</dd>
            <dt>Interpretation</dt><dd>{i.interpretation_status.replace(/_/g, " ").toUpperCase()}</dd>
            <dt>Before</dt><dd>{neighbour(e.preceded_by)}</dd>
            <dt>After</dt><dd>{neighbour(e.followed_by)}</dd>
            <dt>Fuel coverage</dt><dd>{num(e.fuel_coverage_pct as number, 1)} % of rows</dd>
          </dl>
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

      <div className="section">
        <Panel title="Operating timeline around the event"
          question={`What was the vehicle doing before, during and after? Window ${ts(data.timeline.window_start)} – ${clock(data.timeline.window_end ?? "")}, up to 2 min either side, clipped to the recording session.`}>
          <TimelineMultiples timeline={data.timeline} spans={[{ start: e.event_start, end: e.event_end, highlight: true }]} />
        </Panel>
      </div>

      {showEvidence && <EvidenceDrawer eventId={e.event_id} onClose={() => setShowEvidence(false)} />}
      {showAnalyst && <AnalystPanel scope="event" eventId={e.event_id} initialQuestion={sp.get("q")} onClose={() => setShowAnalyst(false)} />}
    </div>
  );
}
