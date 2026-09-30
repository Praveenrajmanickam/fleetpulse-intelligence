import { Link, useParams } from "react-router-dom";
import { useState } from "react";
import AnalystPanel from "../components/AnalystPanel";
import { C, TimelineMultiples } from "../components/charts";
import { EventTable, Kpi, Loading, Panel } from "../components/ui";
import { api } from "../lib/api";
import { clock, duration, int, num, shortSession, ts, useApi } from "../lib/format";

export default function SessionDetail() {
  const { sessionId = "" } = useParams();
  const { data, error } = useApi(() => api.session(sessionId), [sessionId]);
  const [showAnalyst, setShowAnalyst] = useState(false);
  if (error) {
    return (
      <div className="page">
        <div className="crumbs"><Link to="/sessions">Sessions</Link></div>
        <h1>Session not found</h1>
        <p className="lede">{error.message}</p>
      </div>
    );
  }
  if (!data) return <Loading />;
  const s = data.session;
  const extended = data.events.filter((e) => e.attention_group === "extended");
  return (
    <div className="page">
      <div className="crumbs"><Link to="/">Overview</Link> / <Link to="/sessions">Sessions</Link> / {shortSession(s.recording_session_id)}</div>
      <div className="page-head spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow">Recording session</div>
          <h1>{ts(s.session_start)} – {clock(s.session_end)}</h1>
          <p className="lede mono small">{s.recording_session_id}</p>
        </div>
        <button className="btn primary" onClick={() => setShowAnalyst(true)}>Ask FleetPulse AI</button>
      </div>
      {showAnalyst && <AnalystPanel scope="session" sessionId={s.recording_session_id} onClose={() => setShowAnalyst(false)} />}

      <div className="grid g4">
        <Kpi label="Engine on" value={duration(s.engine_on_duration_s)} sub={`recording ${duration(s.session_duration_s)}`} />
        <Kpi label="Moving" swatch={C.moving} value={duration(s.moving_duration_s)} sub={`${num(s.distance_km, 1)} km`} />
        <Kpi label="Stationary, engine on" swatch={C.stationary} value={duration(s.stationary_engine_on_duration_s)}
          sub={`${num(s.stationary_pct_of_engine_on_time, 1)} % of engine-on time`} />
        <Kpi label="Fuel" value={num(s.fuel_consumed_l, 2)} unit="L"
          sub={`${num(s.fuel_while_stationary_l, 2)} L while stationary · counter ${num(s.fuel_counter_l, 2)} L`} />
      </div>

      <div className="section">
        <Panel title="Session operating timeline"
          question={`When did the vehicle drive and when did it stand with the engine running? ${int(s.stationary_events)} events shaded; longest ${duration(s.longest_stationary_event_s)}.`}>
          <TimelineMultiples timeline={data.timeline} height={90}
            spans={data.events.map((e) => ({ start: e.event_start, end: e.event_end, highlight: e.attention_group === "extended" }))} />
        </Panel>
      </div>

      {extended.length > 0 && (
        <div className="section">
          <div className="section-title"><h2>Extended events in this session</h2><span className="count">{extended.length}</span></div>
          <EventTable events={extended} compact />
        </div>
      )}

      <div className="section">
        <div className="section-title">
          <h2>All stationary engine-on events</h2><span className="count">{data.events.length}</span>
          <Link to={`/idle/events?session_id=${encodeURIComponent(s.recording_session_id)}`} style={{ marginLeft: "auto" }} className="small">
            Filter in Event Explorer →
          </Link>
        </div>
        <EventTable events={data.events} compact />
      </div>
    </div>
  );
}
