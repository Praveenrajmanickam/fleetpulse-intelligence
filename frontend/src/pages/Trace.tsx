import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Provenance } from "../components/foundation/Provenance";
import { Skeleton } from "../components/foundation/Skeleton";
import { ErrorBox, Loading } from "../components/ui";
import { SystemIcon } from "../components/vehicle/signals";
import { signalsApi, type SignalTrace } from "../lib/api";
import { duration, num, ts, useApi } from "../lib/format";

function Step({ n, layer, title, children, tone }: { n: number; layer: string; title: string; children: ReactNode; tone?: string }) {
  return (
    <li className={`tstep ${tone ?? ""}`}>
      <div className="tstep-head">
        <span className="tstep-n mono">{String(n).padStart(2, "0")}</span>
        <span className="tstep-title">{title}</span>
        <span className={`layer layer-${layer.toLowerCase()}`}>{layer}</span>
      </div>
      <div className="tstep-body">{children}</div>
    </li>
  );
}

function Bytes({ t }: { t: SignalTrace }) {
  const used = new Set(t.steps.decode.bytes.map((b) => b.byte));
  return (
    <div className="bytes" role="list" aria-label="CAN data bytes">
      {t.steps.raw_can.data.map((b, i) => (
        <span key={i} role="listitem" className={`byte${used.has(i + 1) ? " used" : ""}`}>
          <span className="bidx">B{i + 1}</span>
          <span className="bval mono">{b === null ? "--" : b.toString(16).toUpperCase().padStart(2, "0")}</span>
        </span>
      ))}
    </div>
  );
}

function fmtVal(v: number | null, unit: string) {
  if (v === null) return "no value";
  return `${Number.isInteger(v) ? v : num(v, 3)} ${unit}`;
}

function TraceBody({ t }: { t: SignalTrace }) {
  const s = t.steps;
  const d = s.decode;
  const rawBytes = d.bytes.map((b) => b.hex).join(", ");
  const ok = t.checks.decoder_matches_silver_event && t.checks.decoder_matches_telemetry;
  return (
    <>
      <div className="trace-checks">
        <span className={`check ${t.checks.decoder_matches_silver_event ? "ok" : "bad"}`}>
          {t.checks.decoder_matches_silver_event ? "✓" : "✗"} Production decoder = Silver signal event (same source line)
        </span>
        <span className={`check ${t.checks.decoder_matches_telemetry ? "ok" : "bad"}`}>
          {t.checks.decoder_matches_telemetry ? "✓" : "✗"} = Silver telemetry value at the grid row
        </span>
        <span className="small muted">Every value below is read from repository data; nothing is illustrative.</span>
      </div>
      <ol className="trace-flow">
        <Step n={1} layer="Bronze" title="Raw CAN frame">
          <div className="mono tbig">{s.raw_can.can_id_hex}</div>
          <Bytes t={t} />
          <div className="tmeta mono">{s.raw_can.timestamp.replace("T", " ")} · DLC {s.raw_can.dlc}</div>
          <div className="tmeta mono">{s.raw_can.source_file} : line {s.raw_can.source_line}</div>
        </Step>
        <Step n={2} layer="J1939" title="CAN ID → PGN">
          <div className="mono tbig">PGN {s.j1939.pgn} <span className="muted">{s.j1939.pgn_hex}</span></div>
          <div className="tsub">{s.j1939.message} · {s.j1939.message_name}</div>
          <div className="tmeta mono">priority {s.j1939.priority} · PF {s.j1939.pdu_format} · PS {s.j1939.pdu_specific} · {s.j1939.is_pdu2 ? "PDU2" : "PDU1"} · SA {s.j1939.source_address}</div>
        </Step>
        <Step n={3} layer="J1939" title="SPN">
          <div className="mono tbig">SPN {s.spn.spn}</div>
          <div className="tsub">{s.spn.label}</div>
          <div className="tmeta">{s.spn.reference}</div>
        </Step>
        <Step n={4} layer="Decode" title="Byte decoding">
          <div className="tsub">{d.position} · {d.byte_order}</div>
          <div className="mono tbig">{rawBytes} → raw {d.raw ?? "—"}</div>
          {d.kind === "discrete" && <div className="tmeta">bits {d.start_bit}–{(d.start_bit ?? 1) + 1} of byte {d.start_byte}</div>}
        </Step>
        <Step n={5} layer="Decode" title="Scaling → physical value" tone="accent">
          {d.kind === "measured" && <div className="tsub mono">{d.formula}</div>}
          <div className="mono tbig">{fmtVal(s.physical.value, s.physical.unit)}</div>
          <div className="tmeta">status <b>{s.physical.status.replace(/_/g, " ")}</b>{s.physical.state_label ? ` · ${s.physical.state_label}` : ""}</div>
        </Step>
        <Step n={6} layer="Silver" title="Silver signal event">
          {s.silver_signal_event ? (
            <>
              <div className="mono tbig">{s.silver_signal_event.signal_name}</div>
              <div className="tsub mono">raw {s.silver_signal_event.signal_raw} → {fmtVal(s.silver_signal_event.signal_value, s.physical.unit)}</div>
              <div className="tmeta mono">status {s.silver_signal_event.signal_status} · build {s.silver_signal_event.silver_build_id}</div>
            </>
          ) : <div className="tsub">No matching signal event.</div>}
        </Step>
        <Step n={7} layer="Silver" title="Silver telemetry (100 ms grid)">
          <div className="mono tbig">{s.silver_telemetry.column} = {fmtVal(s.silver_telemetry.value, s.physical.unit)}</div>
          <div className="tsub mono">{s.silver_telemetry.grid_timestamp.replace("T", " ")}</div>
          <div className="tmeta">age {num(s.silver_telemetry.age_ms, 1)} ms ≤ {s.silver_telemetry.freshness_limit_ms} ms → {s.silver_telemetry.freshness} · as-of, no look-ahead</div>
        </Step>
        <Step n={8} layer="Gold" title="Gold event">
          <div className="tbig">Stationary engine-on event</div>
          <div className="tsub mono">{ts(s.gold_event.event_start)} · {duration(s.gold_event.duration_seconds)} · {num(s.gold_event.fuel_consumed_l, 3)} L</div>
          <div className="tmeta">Rule {s.gold_event.rule_version}: {s.gold_event.signal_role.text}</div>
        </Step>
        <Step n={9} layer="Intelligence" title="Business intelligence" tone="attention">
          <div className="tbig">{s.intelligence.attention_group === "extended" ? "Operational investigation" : "Routine observation"}</div>
          <div className="tsub">{s.intelligence.text}</div>
          <div className="tmeta">Detection {s.gold_event.detection_confidence} · interpretation {s.gold_event.interpretation_status.replace(/_/g, " ")}</div>
        </Step>
      </ol>
      {!ok && <p className="caveat quality_warning">A consistency check did not pass for this trace.</p>}
    </>
  );
}

export default function Trace() {
  const [sp, setSp] = useSearchParams();
  const signal = sp.get("signal") ?? "engine_speed_rpm";
  const eventId = sp.get("event_id") ?? undefined;
  const catalog = useApi(signalsApi.catalog, []);
  const trace = useApi(() => signalsApi.trace(signal, eventId), [signal, eventId]);
  if (catalog.error) return <ErrorBox error={catalog.error} onRetry={catalog.reload} />;
  if (!catalog.data) return <Loading />;
  const set = (name: string) => {
    const n = new URLSearchParams(sp);
    n.set("signal", name);
    setSp(n, { replace: true });
  };
  const t = trace.data;
  return (
    <div className="page trace-page">
      <div className="page-head spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow">Architecture · Signal traceability</div>
          <h1>From CAN frame to Gold intelligence</h1>
          <p className="lede">
            One real value followed through every layer: the Bronze frame, J1939 decoding with the production decoder, Silver lineage,
            the Gold event and its business meaning. Anchored on the Silver telemetry row at the midpoint of
            {t ? <> the <Link to={`/idle/events/${encodeURIComponent(t.anchor.event_id)}`}>{t.anchor.default_reference_event ? "reference event" : "selected event"}</Link></> : " the event"}.
          </p>
        </div>
        <Provenance kind="measured" boxed />
      </div>
      <div className="trace-signals" role="toolbar" aria-label="Signal to trace">
        {catalog.data.items.map((c) => (
          <button key={c.name} type="button" className={`chip${c.name === signal ? " on" : ""}`} aria-pressed={c.name === signal} onClick={() => set(c.name)}>
            <SystemIcon system={c.system} size={13} /> {c.label}
          </button>
        ))}
      </div>
      {trace.error ? <ErrorBox error={trace.error} onRetry={trace.reload} embedded /> : !t || trace.loading ? (
        <div className="trace-flow" aria-busy="true">{Array.from({ length: 9 }, (_, i) => <Skeleton key={i} height={150} />)}</div>
      ) : <TraceBody t={t} />}
    </div>
  );
}
