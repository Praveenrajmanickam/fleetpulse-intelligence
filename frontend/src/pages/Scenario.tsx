import { useState } from "react";
import AnalystPanel from "../components/AnalystPanel";
import { ErrorBox, Kpi, Loading, Panel } from "../components/ui";
import { api, type ScenarioRequest, type ScenarioResult } from "../lib/api";
import { num, useApi } from "../lib/format";

type Basis = "all_stationary" | "extended" | "over_15";

export default function Scenario() {
  const summary = useApi(api.idleSummary, []);
  const [pct, setPct] = useState("");
  const [price, setPrice] = useState("");
  const [currency, setCurrency] = useState("EUR");
  const [basis, setBasis] = useState<Basis>("all_stationary");
  const [extra, setExtra] = useState(false);
  const [obsDays, setObsDays] = useState("");
  const [projDays, setProjDays] = useState("");
  const [note, setNote] = useState("");
  const [result, setResult] = useState<ScenarioResult>();
  const [err, setErr] = useState<Error>();
  const [busy, setBusy] = useState(false);
  const [showAnalyst, setShowAnalyst] = useState(false);
  const [lastBody, setLastBody] = useState<ScenarioRequest>();

  if (summary.error) return <ErrorBox error={summary.error} onRetry={summary.reload} />;
  if (!summary.data) return <Loading />;
  const sm = summary.data;

  const ready = pct !== "" && price !== "" && (!extra || (obsDays !== "" && projDays !== "" && note.trim() !== ""));
  const run = async () => {
    const body: ScenarioRequest = { avoidable_pct: Number(pct), fuel_price_per_l: Number(price), currency, basis: "all_stationary" };
    if (basis !== "all_stationary") {
      body.basis = "events";
      body.event_ids = (basis === "extended" ? sm.extended_events : sm.highlighted_events).map((e) => e.event_id);
    }
    if (extra) body.extrapolation = { observed_period_days: Number(obsDays), projection_days: Number(projDays), assumption_note: note };
    setBusy(true); setErr(undefined);
    try { setResult(await api.scenario(body)); setLastBody(body); } catch (x) { setResult(undefined); setErr(x as Error); } finally { setBusy(false); }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div className="eyebrow">Idle Intelligence · Impact Scenario</div>
        <h1>What-if scenario on observed stationary fuel</h1>
        <p className="lede">
          Enter your own assumptions. FleetPulse applies them to observed Gold values using the validated Gold scenario model on the server.
          The telemetry cannot say which stationary running was avoidable; this page only shows the consequence of your assumption.
        </p>
      </div>
      <div className="scenario-banner">Scenario / estimate — not observed savings</div>

      <div className="grid g2 section">
        <Panel title="Assumptions (operator input)" question="No defaults: both values must be entered.">
          <div className="filters" style={{ flexDirection: "column", alignItems: "stretch" }}>
            <label className="field">Basis (observed)
              <select value={basis} onChange={(e) => setBasis(e.target.value as Basis)}>
                <option value="all_stationary">All stationary engine-on fuel · {num(sm.stationary_fuel_consumed_l, 2)} L ({sm.events_total} events)</option>
                <option value="extended">Extended events only · {sm.extended_events.length} events ≥ 5 min</option>
                <option value="over_15">Events over 15 min · {sm.highlighted_events.length} events</option>
              </select>
            </label>
            <div className="row">
              <label className="field">Assumed avoidable share %
                <input type="number" min={0} max={100} step={1} value={pct} placeholder="required" onChange={(e) => setPct(e.target.value)} />
              </label>
              <label className="field">Fuel price per litre
                <input type="number" min={0} step={0.01} value={price} placeholder="required" onChange={(e) => setPrice(e.target.value)} />
              </label>
              <label className="field">Currency
                <input value={currency} maxLength={8} style={{ width: 70 }} onChange={(e) => setCurrency(e.target.value)} />
              </label>
            </div>
            <label className="row small" style={{ color: "var(--text-2)" }}>
              <input type="checkbox" checked={extra} onChange={(e) => setExtra(e.target.checked)} /> Add an extrapolation (optional)
            </label>
            {extra && (
              <div className="row">
                <label className="field">Observed period days
                  <input type="number" min={0} step={1} value={obsDays} onChange={(e) => setObsDays(e.target.value)} />
                </label>
                <label className="field">Projection days
                  <input type="number" min={0} step={1} value={projDays} onChange={(e) => setProjDays(e.target.value)} />
                </label>
                <label className="field" style={{ flex: 1, minWidth: 220 }}>Assumption note (required)
                  <input value={note} placeholder="why this window represents the projection period" onChange={(e) => setNote(e.target.value)} />
                </label>
              </div>
            )}
            <div className="row">
              <button className="btn primary" disabled={!ready || busy} onClick={run}>{busy ? "Calculating…" : "Run scenario"}</button>
              {!ready && <span className="small muted">Enter the assumed share and the fuel price to run.</span>}
            </div>
          </div>
          {err && <p className="error" style={{ padding: 0 }}>{err.message}</p>}
        </Panel>

        <Panel title="Scenario result" question="Calculated by the Gold scenario module on the server; displayed as returned.">
          {!result && <p className="muted small">No scenario yet.</p>}
          {result && (
            <>
              <div className="scenario-banner" style={{ marginBottom: 14 }}>{result.label}</div>
              <div className="grid g2">
                <Kpi label="Observed stationary fuel (basis)" value={num(result.inputs.observed_stationary_fuel_l, 3)} unit="L" sub="observed · Gold" provenance="measured" />
                <Kpi label="Assumed avoidable share" value={num(result.inputs.operator_selected_avoidable_percentage, 1)} unit="%" sub="your assumption" provenance="assumed" />
                <Kpi label="Scenario fuel" value={num(result.scenario_avoidable_fuel_l, 3)} unit="L" sub="estimate, not observed" provenance="derived" />
                <Kpi label="Scenario cost exposure" value={num(result.scenario_cost_exposure, 2)} unit={result.inputs.currency}
                  sub={`at ${num(result.inputs.operator_selected_fuel_price_per_l, 2)} ${result.inputs.currency}/L (your input)`} provenance="derived" />
              </div>
              {result.projection && (
                <div style={{ marginTop: 14 }}>
                  <div className="scenario-banner" style={{ marginBottom: 10 }}>{result.projection.label}</div>
                  <div className="grid g2">
                    <Kpi label={`Projected fuel · ${num(result.projection.projection_days, 0)} days`} value={num(result.projection.projected_avoidable_fuel_l, 1)} unit="L"
                      sub={`scale × ${num(result.projection.scale_factor, 2)} from ${num(result.projection.observed_period_days, 0)} observed days`} />
                    <Kpi label="Projected cost exposure" value={num(result.projection.projected_cost_exposure, 0)} unit={result.inputs.currency}
                      sub={`Assumption: ${result.projection.assumption_note}`} />
                  </div>
                </div>
              )}
              <dl className="dl" style={{ marginTop: 14 }}>
                <dt>Basis</dt><dd>{result.basis.description}</dd>
                <dt>Observed values</dt><dd>{result.observed_values.join(", ")}</dd>
                <dt>Assumed values</dt><dd>{result.assumed_values.join(", ")}</dd>
                <dt>Calculated by</dt><dd>{result.calculated_by}</dd>
                <dt>Gold build</dt><dd>{result.build.gold_build_id}</dd>
              </dl>
              <div className="row" style={{ marginTop: 12 }}>
                <button className="btn" onClick={() => setShowAnalyst(true)}>Explain with FleetPulse AI</button>
                <span className="small muted">The analyst explains the Gold result; it does not recalculate it.</span>
              </div>
            </>
          )}
        </Panel>
      </div>
      {showAnalyst && lastBody && (
        <AnalystPanel scope="scenario" onClose={() => setShowAnalyst(false)}
          scenario={{ avoidable_pct: lastBody.avoidable_pct, fuel_price_per_l: lastBody.fuel_price_per_l, currency: lastBody.currency,
            event_ids: lastBody.event_ids }} />
      )}
      <p className="statement caution section">
        The observed stationary fuel is measured. The share you enter is an assumption; no part of this result was observed.
        One vehicle, Part 3 observation window only.
      </p>
    </div>
  );
}
