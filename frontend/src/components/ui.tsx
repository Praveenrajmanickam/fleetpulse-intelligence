import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import type { IdleEvent } from "../lib/api";
import { clock, duration, neighbour, num, shortSession, tag, ts } from "../lib/format";

export function Kpi(props: { label: ReactNode; value: ReactNode; unit?: string; sub?: ReactNode; swatch?: string; to?: string }) {
  const nav = useNavigate();
  return (
    <div className={`kpi${props.to ? " link" : ""}`} onClick={props.to ? () => nav(props.to!) : undefined}
      role={props.to ? "link" : undefined}>
      <div className="kpi-label">
        {props.swatch && <span className="swatch" style={{ background: props.swatch }} />}
        {props.label}
      </div>
      <div className="kpi-value">
        {props.value}
        {props.unit && <small>{props.unit}</small>}
      </div>
      {props.sub && <div className="kpi-sub">{props.sub}</div>}
    </div>
  );
}

export function Panel(props: { title: ReactNode; question?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${props.className ?? ""}`}>
      <div className="panel-head">
        <h2>{props.title}</h2>
        {props.right}
      </div>
      {props.question && <p className="panel-q">{props.question}</p>}
      {props.children}
    </section>
  );
}

type Tone = "good" | "warning" | "serious" | "info" | "neutral";
export const Badge = ({ tone, children }: { tone: Tone; children: ReactNode }) => (
  <span className={`badge ${tone}`}>
    <span className="dot" />
    {children}
  </span>
);

const CONF_TONE: Record<string, Tone> = { high: "good", medium: "warning", low: "serious" };
export const ConfidenceBadge = ({ level }: { level: string }) => (
  <Badge tone={CONF_TONE[level] ?? "neutral"}>{level} confidence</Badge>
);
export const InterpretationBadge = ({ status }: { status: string }) => (
  <Badge tone="neutral">{status.replace(/_/g, " ")}</Badge>
);
export const AttentionBadge = ({ group }: { group: string }) =>
  group === "extended" ? <Badge tone="warning">Extended · review</Badge> : <Badge tone="neutral">Routine / short</Badge>;

export const Loading = () => <div className="loading">Loading Gold data…</div>;
export const ErrorBox = ({ error }: { error: Error }) => <div className="error">Could not load: {error.message}</div>;

export const Tags = ({ tags }: { tags: string[] | null }) => (
  <span className="row" style={{ gap: 4 }}>
    {(tags ?? []).filter((t) => t !== "over_15_min").map((t) => <span className="tag" key={t}>{tag(t)}</span>)}
  </span>
);

export type SortKey = "event_start" | "duration_seconds" | "fuel_consumed_l" | "average_fuel_rate_lph" | "average_engine_rpm" | "coolant_start_c";

export function EventTable(props: {
  events: IdleEvent[];
  sort?: string;
  onSort?: (k: SortKey) => void;
  showSession?: boolean;
  compact?: boolean;
}) {
  const nav = useNavigate();
  const H = ({ k, children, num: isNum }: { k?: SortKey; children: ReactNode; num?: boolean }) => {
    const active = k && props.sort?.replace("-", "") === k;
    const arrow = active ? (props.sort!.startsWith("-") ? " ↓" : " ↑") : "";
    return (
      <th className={`${isNum ? "num" : ""} ${k && props.onSort ? "sortable" : ""}`}
        onClick={k && props.onSort ? () => props.onSort!(k) : undefined}>
        {children}{arrow}
      </th>
    );
  };
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <H k="event_start">Start</H>
            {props.showSession && <th>Session</th>}
            <H k="duration_seconds" num>Duration</H>
            <H k="fuel_consumed_l" num>Fuel L</H>
            <H k="average_fuel_rate_lph" num>Avg L/h</H>
            <H k="average_engine_rpm" num>RPM</H>
            {!props.compact && <th className="num">Load %</th>}
            <H k="coolant_start_c" num>Coolant °C</H>
            <th>Preceded / followed</th>
            <th>Tags</th>
            <th>Detection</th>
            {!props.compact && <th>Interpretation</th>}
          </tr>
        </thead>
        <tbody>
          {props.events.map((e) => (
            <tr key={e.event_id} className={`clickable${e.duration_class === "over_15_min" ? " hl" : ""}`}
              onClick={() => nav(`/idle/events/${encodeURIComponent(e.event_id)}`)}>
              <td className="strong mono">{ts(e.event_start)}</td>
              {props.showSession && <td className="mono">{shortSession(e.recording_session_id)}</td>}
              <td className="num strong">{duration(e.duration_seconds)}</td>
              <td className="num">{num(e.fuel_consumed_l, 3)}</td>
              <td className="num">{num(e.average_fuel_rate_lph, 2)}</td>
              <td className="num">{num(e.average_engine_rpm, 0)}</td>
              {!props.compact && <td className="num">{num(e.average_engine_load_pct, 1)}</td>}
              <td className="num">{num(e.coolant_start_c, 0)} → {num(e.coolant_end_c, 0)}</td>
              <td>{neighbour(e.preceded_by)} / {neighbour(e.followed_by)}</td>
              <td><Tags tags={e.descriptive_tags} /></td>
              <td><ConfidenceBadge level={e.detection_confidence} /></td>
              {!props.compact && <td><InterpretationBadge status={e.interpretation_status} /></td>}
            </tr>
          ))}
          {props.events.length === 0 && (
            <tr><td colSpan={12} className="muted">No events match these filters.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export const TimeRange = ({ start, end }: { start: string; end: string }) => (
  <span className="mono">{ts(start)} – {clock(end)}</span>
);
