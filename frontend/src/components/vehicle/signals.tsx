/** Shared signal building blocks: health state badge, system icons, signal rows. */
import { Link } from "react-router-dom";
import type { HealthState, SignalCatalogItem, SignalHealthItem, SystemId } from "../../lib/api";
import { num } from "../../lib/format";

export const HEALTH_META: Record<HealthState, { label: string; glyph: string; tone: string }> = {
  available: { label: "Available", glyph: "✓", tone: "good" },
  partial: { label: "Partial", glyph: "◐", tone: "attention" },
  unavailable: { label: "Unavailable", glyph: "⊘", tone: "neutral" },
  quality_warning: { label: "Quality warning", glyph: "⚠", tone: "attention" },
};

/** Icon + text + colour: never colour alone. */
export function HealthBadge({ state, compact = false }: { state: HealthState; compact?: boolean }) {
  const m = HEALTH_META[state];
  return (
    <span className={`hstate ${m.tone}`} data-state={state} title={m.label}>
      <span aria-hidden="true" className="hglyph">{m.glyph}</span>
      {compact ? <span className="sr-only">{m.label}</span> : <span>{m.label}</span>}
    </span>
  );
}

export function SystemIcon({ system, size = 16 }: { system: SystemId; size?: number }) {
  const p = { width: size, height: size, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.4,
    strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (system) {
    case "engine":
      return <svg {...p}><rect x="2" y="5" width="9" height="7" rx="1" /><path d="M4 5V3h5v2M11 7h2v3h-2M2 8H1" /></svg>;
    case "fuel":
      return <svg {...p}><rect x="2.5" y="2.5" width="7" height="11" rx="1" /><path d="M9.5 6h2l1.5 1.5V12a1 1 0 0 1-2 0V9h-1.5M4.5 5h3" /></svg>;
    case "cooling":
      return <svg {...p}><path d="M8 2v9M6 4h2M6 6.5h2" /><circle cx="8" cy="12" r="2" /></svg>;
    case "driver":
      return <svg {...p}><circle cx="8" cy="8" r="5.5" /><circle cx="8" cy="8" r="1.4" /><path d="M2.6 7h4M9.4 7h4M8 9.4v4" /></svg>;
    case "motion":
      return <svg {...p}><circle cx="8" cy="8" r="5.5" /><circle cx="8" cy="8" r="2" /><path d="M8 2.5V6M8 10v3.5M2.5 8H6M10 8h3.5" /></svg>;
    case "environment":
      return <svg {...p}><path d="M2 6h8a2 2 0 1 0-2-2M2 9h11a2 2 0 1 1-2 2M2 12h5" /></svg>;
  }
}

export function SignalRow({ item, health, active, onSelect, to }: {
  item: SignalCatalogItem; health?: SignalHealthItem; active?: boolean; onSelect?: () => void; to?: string;
}) {
  const pct = health?.scopes.engine_running_rows.available_pct;
  const body = (
    <>
      <div className="sigrow-main">
        <span className="sigrow-label">{item.label}</span>
        <span className="sigrow-id mono">{item.message} · PGN {item.pgn} · SPN {item.spn}</span>
      </div>
      <div className="sigrow-side">
        {health && <HealthBadge state={health.state} compact />}
        {pct !== undefined && pct !== null && <span className="mono small muted">{num(pct, 1)}%</span>}
      </div>
    </>
  );
  if (to) return <Link to={to} className={`sigrow${active ? " active" : ""}`}>{body}</Link>;
  return (
    <button type="button" className={`sigrow${active ? " active" : ""}`} aria-pressed={active} onClick={onSelect}>
      {body}
    </button>
  );
}

export function signalMaps(items?: SignalCatalogItem[], health?: SignalHealthItem[]) {
  const catalog = new Map((items ?? []).map((i) => [i.name, i]));
  const hmap = new Map((health ?? []).map((h) => [h.name, h]));
  return { catalog, hmap };
}
