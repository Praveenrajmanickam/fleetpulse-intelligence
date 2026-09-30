import { Link, useSearchParams } from "react-router-dom";
import { Provenance } from "../components/foundation/Provenance";
import { ErrorBox, Loading } from "../components/ui";
import VehicleVisualization from "../components/vehicle/VehicleVisualization";
import { HealthBadge, SignalRow, SystemIcon, signalMaps } from "../components/vehicle/signals";
import { VEHICLE_SYSTEMS, type VehicleSystemId } from "../components/vehicle/types";
import { signalsApi, type SignalCatalogItem, type SignalHealthItem } from "../lib/api";
import { int, num, useApi } from "../lib/format";

function Detail({ c, h }: { c: SignalCatalogItem; h?: SignalHealthItem }) {
  const d = c.decode;
  const run = h?.scopes.engine_running_rows;
  return (
    <div className="sensor-detail">
      <div className="eyebrow">{c.system_label}</div>
      <h2 className="sensor-title">{c.label}</h2>
      <p className="panel-q" style={{ marginTop: 4 }}>{c.description}</p>

      <div className="spec-grid">
        <section className="spec">
          <h3>J1939</h3>
          <div className="spec-big mono">PGN {c.pgn}</div>
          <div className="mono small text-2">{c.pgn_hex} · {c.message} — {c.message_name}</div>
          <div className="spec-big mono" style={{ marginTop: 10 }}>SPN {c.spn}</div>
          <div className="small text-2">{c.label}</div>
        </section>
        <section className="spec">
          <h3>Decode</h3>
          <dl className="dl compact-dl">
            <dt>Position</dt><dd>{d.position}</dd>
            <dt>Byte order</dt><dd>{d.byte_order}</dd>
            {d.kind === "measured" ? (
              <>
                <dt>Resolution</dt><dd>{d.resolution} {c.unit}/bit</dd>
                <dt>Offset</dt><dd>{d.offset} {c.unit}</dd>
                <dt>Formula</dt><dd>{d.formula}</dd>
                <dt>Range</dt><dd>{d.operating_range?.[0]} … {d.operating_range?.[1]} {c.unit}</dd>
              </>
            ) : (
              <>
                <dt>Bits</dt><dd>{d.start_bit}–{(d.start_bit ?? 1) + d.length_bits - 1} of byte {d.start_byte}</dd>
                <dt>States</dt><dd>{Object.entries(d.states ?? {}).map(([k, v]) => `${k} = ${v}`).join(" · ")} · 2 = error · 3 = not available</dd>
              </>
            )}
            <dt>Unit</dt><dd>{c.unit}</dd>
            <dt>FMS rate</dt><dd>{c.fms_period_ms} ms (fresh ≤ {c.freshness_limit_ms} ms)</dd>
          </dl>
        </section>
      </div>

      <section className="spec">
        <div className="spread"><h3>Availability &amp; quality</h3>{h && <HealthBadge state={h.state} />}</div>
        {run ? (
          <>
            <div className="avail-bar" role="img" aria-label={`${num(run.available_pct, 2)} percent available`}>
              <i style={{ width: `${run.available_pct ?? 0}%` }} />
            </div>
            <div className="small text-2" style={{ marginTop: 6 }}>
              Valid &amp; fresh in <b className="mono">{num(run.available_pct, 2)} %</b> of {int(run.rows)} engine-running Silver rows
              · status {Object.entries(run.status_counts).map(([k, v]) => `${k.replace(/_/g, " ")} ${int(v)}`).join(" · ")}
            </div>
            <div className="small muted" style={{ marginTop: 4 }}><Provenance kind="derived" /> Historical recording (Part 3). Signal health ≠ mechanical health.</div>
          </>
        ) : <p className="small muted">Loading data availability…</p>}
        {c.caveats.map((x) => (
          <p key={x.text} className={`caveat ${x.level}`}>
            <span aria-hidden="true">{x.level === "quality_warning" ? "⚠" : x.level === "unavailable" ? "⊘" : "ℹ"}</span> {x.text}
            <span className="muted"> ({x.source})</span>
          </p>
        ))}
      </section>

      <section className="spec">
        <h3>Related intelligence</h3>
        <p className="small" style={{ margin: 0 }}>{c.gold_use.text}</p>
        <p className="small muted" style={{ margin: "6px 0 0" }}>Reference: {c.reference}</p>
      </section>

      <div className="row" style={{ marginTop: 12 }}>
        <Link className="btn primary" to={`/vehicle/trace?signal=${c.name}`}>Trace signal → Gold</Link>
        <Link className="btn" to="/vehicle/signal-health">Signal Health</Link>
      </div>
    </div>
  );
}

export default function SensorExplorer() {
  const [sp, setSp] = useSearchParams();
  const catalog = useApi(signalsApi.catalog, []);
  const health = useApi(signalsApi.health, []);
  if (catalog.error) return <ErrorBox error={catalog.error} onRetry={catalog.reload} />;
  if (!catalog.data) return <Loading />;
  const { catalog: cmap, hmap } = signalMaps(catalog.data.items, health.data?.signals);
  const selected = cmap.get(sp.get("signal") ?? "") ?? cmap.get("engine_speed_rpm")!;
  const system = selected.system as VehicleSystemId;
  const pick = (name: string) => setSp({ signal: name }, { replace: true });
  const pickSystem = (s: VehicleSystemId | null) => {
    const first = VEHICLE_SYSTEMS.find((x) => x.id === s)?.signals[0];
    if (first) pick(first);
  };

  return (
    <div className="page">
      <div className="page-head">
        <div className="eyebrow">Vehicle · Sensor explorer</div>
        <h1>Sensor Explorer</h1>
        <p className="lede">
          The {catalog.data.count} J1939/FMS signals FleetPulse decodes, with their verified decoding ({catalog.data.standard}) and
          their availability in the recording. Select a signal, or a region of the vehicle.
        </p>
      </div>
      <div className="sensor-grid">
        <nav className="panel sensor-list" aria-label="Decoded signals">
          {VEHICLE_SYSTEMS.map((s) => (
            <div key={s.id} className="sensor-group">
              <div className={`sensor-group-head${s.id === system ? " active" : ""}`}><SystemIcon system={s.id} /> {s.label}</div>
              {s.signals.map((n) => {
                const c = cmap.get(n);
                return c ? <SignalRow key={n} item={c} health={hmap.get(n)} active={n === selected.name} onSelect={() => pick(n)} /> : null;
              })}
            </div>
          ))}
        </nav>
        <div className="sensor-stage">
          <VehicleVisualization variant="compact" activeSystem={system} onSystemSelect={pickSystem}
            caption={`Vehicle with ${selected.system_label} selected`} />
          <p className="small muted" style={{ textAlign: "center", margin: "4px 0 0" }}>Click a vehicle region to jump to its signals.</p>
        </div>
        <section className="panel sensor-side" aria-live="polite">
          <Detail c={selected} h={hmap.get(selected.name)} />
        </section>
      </div>
    </div>
  );
}
