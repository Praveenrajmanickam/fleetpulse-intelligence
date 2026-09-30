/**
 * FleetPulse design tokens (mirror of the CSS custom properties in styles.css).
 * Use these where CSS variables cannot be used (SVG presentation attributes, Recharts props).
 *
 * Contrast (WCAG) on `surface`: text 16.1, text2 9.6, muted 5.8, accent 8.7, attention 9.1.
 * `faint` (3.3) is for disabled / decorative elements only.
 * Series colours re-validated on `surface` with the dataviz palette validator (all checks pass).
 */
export const color = {
  bg: "#0b0e12",
  surface: "#12161b",
  raised: "#181d24",
  sunken: "#0e1216",
  border: "#232a33",
  borderStrong: "#2f3843",
  axis: "#2f3843",
  text: "#eef2f6",
  text2: "#b4bec9",
  muted: "#8792a0",
  faint: "#5f6a77",
  /** interface accent: links, focus, active navigation, selected vehicle system - not a data colour */
  accent: "#4cc2dc",
  /** attention / investigation (amber) - never "bad", only "look here" */
  attention: "#f2a93b",
  /** validated / healthy data state only */
  good: "#3fb950",
  critical: "#f06a5f",
  /** chart series (validated pair) */
  moving: "#3987e5",
  stationary: "#d95926",
  neutralSeries: "#6b7582",
} as const;

export const font = {
  sans: '"IBM Plex Sans", system-ui, sans-serif',
  mono: '"IBM Plex Mono", ui-monospace, monospace',
} as const;

export const motion = {
  fast: 120,
  base: 200,
  slow: 320,
} as const;

/** Honour the OS "reduce motion" setting in JS-driven animation (e.g. Recharts). */
export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}
