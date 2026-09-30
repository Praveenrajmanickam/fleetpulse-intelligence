import { Link, useSearchParams } from "react-router-dom";
import { Provenance } from "../components/foundation/Provenance";
import { ErrorBox, Loading } from "../components/ui";
import VehicleVisualization from "../components/vehicle/VehicleVisualization";
import { HealthBadge, SystemIcon, signalMaps } from "../components/vehicle/signals";
import { VEHICLE_SYSTEMS, type SignalAnnotation, type VehicleSystemId } from "../components/vehicle/types";
import { signalsApi } from "../lib/api";
import { int, num, useApi } from "../lib/format";

const isSystem = (v: string | null): v is VehicleSystemId => VEHICLE_SYSTEMS.some((s) => s.id === v);

export default function VehicleTwin() {
  const [sp, setSp] = useSearchParams();
  const active = isSystem(sp.get("system")) ? (sp.get("system") as VehicleSystemId) : null;
  const catalog = useApi(signalsApi.catalog, []);
  const health = useApi(signalsApi.health, []);
  const select = (s: VehicleSystemId | null) => setSp(s ? { system: s } : {}, { replace: true });
  if (catalog.error) return <ErrorBox error={catalog.error} onRetry={catalog.reload} />;
  if (!catalog.data) return <Loading />;
  const { catalog: cmap, hmap } = signalMaps(catalog.data.items, health.data?.signals);

  // one data-availability annotation per system (engine-running rows), never a live value
  const annotations: SignalAnnotation[] = VEHICLE_SYSTEMS.map((sys) => {
    const worst = sys.signals.map((n) => hmap.get(n)).filter(Boolean)
      .sort((a, b) => (a!.scopes.engine_running_rows.available_pct ?? 0) - (b!.scopes.engine_running_rows.available_pct ?? 0))[0];
    const label = `${sys.signals.length} signal${sys.signals.length > 1 ? "s" : ""}`;
    return {
      signal: sys.signals[0], label,
      value: worst ? (worst.state === "quality_warning" ? "quality warning" : worst.state === "unavailable" ? "1 unavailable"
        : `${num(worst.scopes.engine_running_rows.available_pct, 1)} % available`) : undefined,
      context: { kind: "dataset" as const, label: "Data availability" }, provenance: "derived" as const,
      quality: worst?.state === "quality_warning" ? ("warning" as const) : worst?.state === "unavailable" ? ("unavailable" as const) : "ok",
    };
  });
  const sys = VEHICLE_SYSTEMS.find((s) => s.id === active);

  return (
    <div className="page twin-page">
      <div className="page-head spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow">Vehicle · Digital twin</div>
          <h1>Vehicle Digital Twin</h1>
          <p className="lede">
            The recorded truck is a Renault T520 6x2. This is a generic European heavy-duty tractor illustration, not an exact OEM model.
            Each system maps to the J1939/FMS signals FleetPulse decodes. Select a system to inspect them.
          </p>
        </div>
        <div className="row">
          <Link className="btn" to="/vehicle/sensors">Sensor Explorer</Link>
          <Link className="btn" to="/vehicle/signal-health">Signal Health</Link>
        </div>
      </div>

      <div className="systabs" role="toolbar" aria-label="Vehicle systems">
        {VEHICLE_SYSTEMS.map((s) => (
          <button key={s.id} type="button" className={`systab${active === s.id ? " active" : ""}`} aria-pressed={active === s.id}
            onClick={() => select(active === s.id ? null : s.id)}>
            <SystemIcon system={s.id} /> {s.label}
          </button>
        ))}
      </div>

      <div className="twin-grid">
        <div className="twin-stage">
          <VehicleVisualization variant="inspect" activeSystem={active} annotations={annotations} onSystemSelect={select}
            caption="Generic heavy-duty tractor with six selectable systems" />
          <div className="stage-foot small muted">
            <Provenance kind="derived" /> Chips show data availability in engine-running rows of the Part 3 recording
            ({health.data ? int(health.data.engine_running_rows) : "…"} Silver rows). Historical, not live.
          </div>
        </div>

        <aside className="twin-side panel" aria-live="polite">
          {!sys ? (
            <>
              <h2>Select a vehicle system</h2>
              <p className="panel-q">Hover a region to highlight it; click (or press Enter) to see its decoded signals.</p>
              <div className="sys-list">
                {VEHICLE_SYSTEMS.map((s) => (
                  <button key={s.id} type="button" className="sys-item" onClick={() => select(s.id)}>
                    <SystemIcon system={s.id} size={18} />
                    <span><b>{s.label}</b><span className="small muted">{s.signals.map((n) => cmap.get(n)?.label ?? n).join(" · ")}</span></span>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="spread">
                <div className="row" style={{ gap: 10 }}><SystemIcon system={sys.id} size={20} /><h2 style={{ fontSize: 18 }}>{sys.label}</h2></div>
                <button className="btn small-btn" onClick={() => select(null)}>Clear</button>
              </div>
              <p className="panel-q" style={{ marginTop: 6 }}>{sys.description}</p>
              <div className="sig-cards">
                {sys.signals.map((n) => {
                  const c = cmap.get(n);
                  const h = hmap.get(n);
                  if (!c) return null;
                  return (
                    <article key={n} className="sig-card">
                      <div className="spread">
                        <h3 style={{ margin: 0, color: "var(--text)", fontFamily: "var(--sans)", fontSize: 14, letterSpacing: 0, textTransform: "none" }}>{c.label}</h3>
                        {h && <HealthBadge state={h.state} />}
                      </div>
                      <dl className="dl compact-dl">
                        <dt>PGN</dt><dd>{c.pgn} · {c.pgn_hex} · {c.message}</dd>
                        <dt>SPN</dt><dd>{c.spn}</dd>
                        <dt>Decode</dt><dd>{c.decode.position}{c.decode.resolution !== undefined ? ` · ${c.decode.resolution} ${c.unit}/bit` : ""}</dd>
                        <dt>Unit</dt><dd>{c.unit}</dd>
                        {h && <><dt>Available</dt><dd>{num(h.scopes.engine_running_rows.available_pct, 2)} % of engine-running rows</dd></>}
                      </dl>
                      {c.caveats.filter((x) => x.level !== "note").map((x) => (
                        <p key={x.text} className={`caveat ${x.level}`}><span aria-hidden="true">{x.level === "quality_warning" ? "⚠" : "⊘"}</span> {x.text}</p>
                      ))}
                      <div className="row" style={{ marginTop: 8 }}>
                        <Link className="btn small-btn" to={`/vehicle/sensors?signal=${n}`}>Sensor details</Link>
                        <Link className="btn small-btn" to={`/vehicle/trace?signal=${n}`}>Trace CAN → Gold</Link>
                      </div>
                    </article>
                  );
                })}
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
