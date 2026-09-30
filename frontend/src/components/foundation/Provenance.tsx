/**
 * Provenance: where a value comes from. FleetPulse's core trust rule is to always distinguish
 *   MEASURED (the data measured it) · DERIVED (FleetPulse computed it from measurements)
 *   ASSUMED (the user supplied it) · AI INTERPRETATION (a model or the analyst explained it).
 * Each kind has a distinct glyph shape as well as a colour, so it is never colour-only.
 */

export type ProvenanceKind = "measured" | "derived" | "assumed" | "ai";

export const PROVENANCE: Record<ProvenanceKind, { label: string; description: string }> = {
  measured: {
    label: "Measured",
    description: "Recorded by the vehicle (J1939/FMS signals) and aggregated by the validated Gold pipeline.",
  },
  derived: {
    label: "Derived",
    description: "Computed by FleetPulse from measured Gold rows (classification, grouping, share or count).",
  },
  assumed: {
    label: "Assumed",
    description: "Supplied by the user as a scenario input. Not observed.",
  },
  ai: {
    label: "AI interpretation",
    description: "Explanatory text from the analyst layer. It explains evidence; it does not measure anything.",
  },
};

function Glyph({ kind }: { kind: ProvenanceKind }) {
  switch (kind) {
    case "measured": // filled circle: an observation
      return <svg viewBox="0 0 10 10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill="currentColor" /></svg>;
    case "derived": // filled diamond: computed from observations
      return <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M5 0.8 9.2 5 5 9.2 0.8 5Z" fill="currentColor" /></svg>;
    case "assumed": // hollow square: an input, not data
      return <svg viewBox="0 0 10 10" aria-hidden="true"><rect x="1.2" y="1.2" width="7.6" height="7.6" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>;
    case "ai": // four-point spark: generated text
      return <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M5 0 6.2 3.8 10 5 6.2 6.2 5 10 3.8 6.2 0 5 3.8 3.8Z" fill="currentColor" /></svg>;
  }
}

export function Provenance({ kind, boxed = false, compact = false }: { kind: ProvenanceKind; boxed?: boolean; compact?: boolean }) {
  const p = PROVENANCE[kind];
  return (
    <span className={`prov ${kind}${boxed ? " boxed" : ""}`} title={`${p.label}: ${p.description}`}
      aria-label={`Provenance: ${p.label}`} role="note" data-provenance={kind}>
      <Glyph kind={kind} />
      {!compact && <span aria-hidden="true">{p.label}</span>}
    </span>
  );
}

export function ProvenanceLegend({ kinds = ["measured", "derived", "assumed", "ai"] as ProvenanceKind[] }) {
  return (
    <div className="prov-legend" aria-label="Value provenance legend">
      {kinds.map((k) => (
        <div key={k}>
          <Provenance kind={k} />
          <span>{PROVENANCE[k].description}</span>
        </div>
      ))}
    </div>
  );
}
