// Charts. Each answers one operational question; one y-axis per chart; values come from the API.
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { useNavigate } from "react-router-dom";
import type { IdleSummary, Timeline } from "../lib/api";
import { duration, hours, num, shortSession } from "../lib/format";
import { color } from "../design/tokens";

const AXIS = color.axis;
const TICK = { fill: color.muted, fontSize: 11, fontFamily: "IBM Plex Mono" };
export const C = { moving: color.moving, stationary: color.stationary, neutral: color.neutralSeries };

/** Engine-on time composition: how much of engine-on time was stationary? */
export function EngineTimeComposition(props: { movingH: number; stationaryH: number; engineOnH: number; stationaryPct: number }) {
  const w = (h: number) => `${(h / props.engineOnH) * 100}%`; // bar geometry only
  return (
    <div>
      <div className="legend">
        <span><i className="swatch" style={{ background: C.moving }} />Moving · {num(props.movingH, 2)} h</span>
        <span><i className="swatch" style={{ background: C.stationary }} />Stationary, engine on · {num(props.stationaryH, 2)} h ({num(props.stationaryPct, 1)} %)</span>
        <span><i className="swatch" style={{ background: color.borderStrong }} />Speed signal unavailable (remainder)</span>
      </div>
      <div className="composition" style={{ background: color.borderStrong }}>
        <div style={{ width: w(props.movingH), background: C.moving }} title={`Moving ${num(props.movingH, 2)} h`} />
        <div style={{ width: w(props.stationaryH), background: C.stationary }} title={`Stationary engine-on ${num(props.stationaryH, 2)} h`} />
      </div>
      <div className="spread small muted" style={{ marginTop: 6 }}>
        <span>0 h</span><span>{num(props.engineOnH, 2)} h engine on</span>
      </div>
    </div>
  );
}

/** Share bar with a Gold-supplied percentage (e.g. stationary fuel share). */
export function ShareBar(props: { pct: number; color: string; label: string }) {
  return (
    <div>
      <div className="legend">
        <span><i className="swatch" style={{ background: props.color }} />{props.label} · {num(props.pct, 2)} %</span>
      </div>
      <div className="composition" style={{ background: color.borderStrong }}>
        <div style={{ width: `${props.pct}%`, background: props.color }} />
      </div>
    </div>
  );
}

/** How are stationary events distributed by length, and which ones merit review? */
export function DurationDistribution({ data }: { data: IdleSummary["duration_distribution"] }) {
  return (
    <div>
      <div className="legend">
        <span><i className="swatch" style={{ background: C.neutral }} />Routine / short</span>
        <span><i className="swatch" style={{ background: C.stationary }} />Extended · review with context</span>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data} margin={{ top: 18, right: 8, bottom: 0, left: 0 }} barCategoryGap="22%">
          <CartesianGrid vertical={false} stroke={AXIS} strokeDasharray="2 4" />
          <XAxis dataKey="label" tick={TICK} axisLine={{ stroke: AXIS }} tickLine={false} />
          <YAxis tick={TICK} axisLine={false} tickLine={false} width={40} />
          <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} content={({ active, payload }) =>
            active && payload?.length ? (
              <div className="tooltip"><b>{payload[0].payload.label}</b><div className="mono">{payload[0].value} events</div></div>
            ) : null} />
          <Bar dataKey="events" isAnimationActive={false} radius={[4, 4, 0, 0]} label={{ position: "top", fill: color.text2, fontSize: 11, fontFamily: "IBM Plex Mono" }}>
            {data.map((d) => <Cell key={d.duration_class} fill={d.attention_group === "extended" ? C.stationary : C.neutral} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Which engine-operation sessions carried the most stationary engine-on time? */
export function SessionStationaryChart({ sessions }: { sessions: IdleSummary["sessions"] }) {
  const nav = useNavigate();
  const data = sessions.map((s) => ({
    id: s.recording_session_id,
    name: shortSession(s.recording_session_id),
    moving: hours(s.moving_duration_s),
    stationary: hours(s.stationary_engine_on_duration_s),
    pct: s.stationary_pct_of_engine_on_time,
    events: s.stationary_events,
    longest: s.longest_stationary_event_s,
  }));
  return (
    <div>
      <div className="legend">
        <span><i className="swatch" style={{ background: C.moving }} />Moving</span>
        <span><i className="swatch" style={{ background: C.stationary }} />Stationary, engine on</span>
        <span className="muted">Click a bar to open the session</span>
      </div>
      <ResponsiveContainer width="100%" height={Math.max(260, data.length * 24 + 40)}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 8 }} barCategoryGap={4}>
          <CartesianGrid horizontal={false} stroke={AXIS} strokeDasharray="2 4" />
          <XAxis type="number" tick={TICK} axisLine={{ stroke: AXIS }} tickLine={false} unit=" h" />
          <YAxis type="category" dataKey="name" tick={TICK} axisLine={false} tickLine={false} width={150} />
          <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const p = payload[0].payload as (typeof data)[number];
            return (
              <div className="tooltip">
                <b>Session {p.name}</b>
                <div className="t-row"><i className="swatch" style={{ background: C.moving }} />moving {num(p.moving, 2)} h</div>
                <div className="t-row"><i className="swatch" style={{ background: C.stationary }} />stationary {num(p.stationary, 2)} h ({num(p.pct, 1)} %)</div>
                <div className="t-row">{p.events} events · longest {duration(p.longest)}</div>
              </div>
            );
          }} />
          <Bar dataKey="moving" stackId="a" isAnimationActive={false} fill={C.moving} stroke={color.surface} strokeWidth={1}
            onClick={(d) => nav(`/sessions/${encodeURIComponent((d as unknown as { id: string }).id)}`)} style={{ cursor: "pointer" }} />
          <Bar dataKey="stationary" stackId="a" isAnimationActive={false} fill={C.stationary} radius={[0, 4, 4, 0]} stroke={color.surface} strokeWidth={1}
            onClick={(d) => nav(`/sessions/${encodeURIComponent((d as unknown as { id: string }).id)}`)} style={{ cursor: "pointer" }} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---- timeline small multiples ----

const toMs = (iso: string) => Date.parse(`${iso}Z`); // logger wall clock kept as-is (rendered in UTC = unchanged)
const fmtClock = (ms: number) => new Date(ms).toISOString().slice(11, 19);

const SERIES = [
  { key: "wheel_speed_kph", label: "Wheel speed", unit: "km/h", q: "Was the vehicle stationary?" },
  { key: "engine_speed_rpm", label: "Engine speed", unit: "rpm", q: "Was the engine running, at what speed?" },
  { key: "fuel_rate_lph", label: "Fuel rate", unit: "L/h", q: "What was the fuel flow while stationary?" },
  { key: "coolant_temp_c", label: "Coolant", unit: "°C", q: "Was the engine warm?" },
] as const;

export interface Span { start: string; end: string; highlight?: boolean }

/** Operating timeline as small multiples (one axis each), with stationary events shaded. */
export function TimelineMultiples(props: { timeline: Timeline; spans: Span[]; height?: number }) {
  const { timeline } = props;
  if (!timeline.available || timeline.points.length === 0) {
    return <p className="muted small">No Silver telemetry available for this window.</p>;
  }
  const data = timeline.points.map((p) => ({ ...p, x: toMs(p.t) }));
  const domain: [number, number] = [data[0].x, data[data.length - 1].x];
  const h = props.height ?? 110;
  return (
    <div>
      <div className="legend">
        <span><i className="swatch" style={{ background: "rgba(217,89,38,0.28)", outline: `1px solid ${color.stationary}` }} />Stationary engine-on event (Gold)</span>
        <span className="muted">Silver 100 ms grid, every {timeline.sampling_step_rows}th row shown · gaps = no fresh/valid value</span>
      </div>
      {SERIES.map((s, i) => (
        <div key={s.key} style={{ marginBottom: 4 }}>
          <div className="spread small">
            <span className="text-2">{s.label} <span className="muted">({s.unit})</span></span>
            <span className="muted">{s.q}</span>
          </div>
          <ResponsiveContainer width="100%" height={h}>
            <LineChart data={data} syncId="tl" margin={{ top: 4, right: 28, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke={AXIS} strokeDasharray="2 4" />
              {props.spans.map((sp) => (
                <ReferenceArea key={sp.start} x1={toMs(sp.start)} x2={toMs(sp.end)} ifOverflow="hidden"
                  fill={C.stationary} fillOpacity={sp.highlight ? 0.16 : 0.1} stroke={sp.highlight ? C.stationary : "none"} strokeOpacity={0.8} />
              ))}
              <XAxis dataKey="x" type="number" domain={domain} scale="time" tickFormatter={fmtClock} tick={TICK}
                axisLine={{ stroke: AXIS }} tickLine={false} hide={i < SERIES.length - 1} minTickGap={60} />
              <YAxis tick={TICK} axisLine={false} tickLine={false} width={44} />
              <Tooltip cursor={{ stroke: color.muted, strokeWidth: 1 }} content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <div className="tooltip">
                    <b className="mono">{fmtClock(label as number)}</b>
                    <div className="mono">{num(payload[0].value as number, s.key === "engine_speed_rpm" ? 0 : 1)} {s.unit}</div>
                  </div>
                ) : null} />
              <Line type="linear" dataKey={s.key} stroke={color.text} strokeWidth={1.5} dot={false} isAnimationActive={false}
                connectNulls={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ))}
    </div>
  );
}
