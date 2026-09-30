/**
 * Generic European heavy-duty 6x2 tractor, cab-over-engine, drawn in cabinet-oblique projection
 * (near side true scale, depth vector up-left) so the side, front and roof faces read as volumes.
 * Not an OEM model: no logos, no brand geometry. System zones are interactive; "x-ray" outlines
 * show where the engine and radiator sit under the cab.
 */
import { useId, type KeyboardEvent, type ReactNode } from "react";
import { color } from "../../design/tokens";
import { PROVENANCE } from "../foundation/Provenance";
import { systemOf, type SignalAnnotation, type VehicleRendererProps, type VehicleSystemId } from "./types";

// ---- geometry ------------------------------------------------------------------------------
const W = 1280;
const H = 690;
const G = 548; // ground line (near side)
const R = 60; // wheel radius
const WY = G - R; // wheel centre y
const AX = { a1: 372, a2: 880, a3: 1012 } as const;
const D = { x: -72, y: -26 }; // depth vector: near side -> far side
const off = (x: number, y: number) => `${x + D.x},${y + D.y}`;

// cab near-side face
const CAB = { x0: 280, x1: 572, top: 124, bot: 436 };

type Callout = { hot: [number, number]; chip: [number, number]; align: "left" | "right" };
const CALLOUTS: Record<VehicleSystemId, Callout> = {
  environment: { hot: [128, 104], chip: [24, 18], align: "left" },
  driver: { hot: [402, 214], chip: [560, 18], align: "left" },
  cooling: { hot: [230, 318], chip: [18, 250], align: "left" },
  engine: { hot: [452, 372], chip: [150, 604], align: "left" },
  fuel: { hot: [684, 470], chip: [540, 604], align: "left" },
  motion: { hot: [AX.a2, WY], chip: [930, 604], align: "left" },
};
const CHIP_W = 292;
const CHIP_H = 70;

const CONTEXT_LABEL: Record<SignalAnnotation["context"]["kind"], string> = {
  reference_event: "Reference event",
  session: "Session",
  dataset: "Observation window",
  catalog: "Decoded signal",
};

// ---- small parts -----------------------------------------------------------------------------
function Wheel({ cx, cy, far = false }: { cx: number; cy: number; far?: boolean }) {
  return (
    <g opacity={far ? 0.55 : 1}>
      <circle cx={cx} cy={cy} r={R} fill={far ? "#0c0f13" : "url(#tyre)"} stroke="#05070a" strokeWidth="2" />
      {!far && (
        <>
          <circle cx={cx} cy={cy} r={R - 11} fill="none" stroke="#2a323c" strokeWidth="1.5" />
          <circle cx={cx} cy={cy} r={R - 22} fill="url(#rim)" stroke="#4a5563" strokeWidth="1.2" />
          {Array.from({ length: 10 }, (_, i) => {
            const a = (i / 10) * Math.PI * 2;
            return <circle key={i} cx={cx + Math.cos(a) * (R - 31)} cy={cy + Math.sin(a) * (R - 31)} r="2.2" fill="#6c7886" />;
          })}
          <circle cx={cx} cy={cy} r="12" fill="#1a2027" stroke="#5c6877" strokeWidth="1.2" />
          <path d={`M${cx - R + 6} ${cy - 12} A ${R - 6} ${R - 6} 0 0 1 ${cx - 12} ${cy - R + 6}`} fill="none"
            stroke="rgba(255,255,255,0.07)" strokeWidth="5" strokeLinecap="round" />
        </>
      )}
    </g>
  );
}

/** Technical backdrop: dotted grid, ground line, axle datum lines. */
function Backdrop() {
  return (
    <g aria-hidden="true">
      <rect width={W} height={H} fill="url(#grid)" opacity="0.55" />
      <ellipse cx="650" cy={G + 6} rx="560" ry="20" fill="#000" opacity="0.55" filter="url(#blur)" />
      <line x1="60" x2={W - 40} y1={G} y2={G} stroke={color.borderStrong} strokeWidth="1" />
      {Object.entries(AX).map(([k, x], i) => (
        <g key={k}>
          <line x1={x} x2={x} y1={G + 6} y2={G + 22} stroke={color.faint} strokeWidth="1" />
          <text x={x} y={G + 36} textAnchor="middle" fill={color.faint} fontSize="10" fontFamily="IBM Plex Mono" letterSpacing="1.5">
            AXLE {i + 1}
          </text>
        </g>
      ))}
      <line x1={AX.a1} x2={AX.a3} y1={G + 22} y2={G + 22} stroke={color.border} strokeWidth="1" strokeDasharray="2 4" />
    </g>
  );
}

// ---- the vehicle body (static) --------------------------------------------------------------
function Body() {
  const { x0, x1, top, bot } = CAB;
  return (
    <g aria-hidden="true">
      {/* far-side wheels and chassis (behind) */}
      <Wheel cx={AX.a2 + D.x} cy={WY + D.y} far />
      <Wheel cx={AX.a3 + D.x} cy={WY + D.y} far />
      <path d={`M${off(300, 420)} L${off(1120, 420)} L${off(1120, 448)} L${off(300, 448)} Z`} fill="#0f1318" stroke="#232a33" />

      {/* chassis rails (near) */}
      <path d={`M300 420 H1120 V448 H300 Z`} fill="url(#rail)" stroke="#2f3843" />
      <path d={`M1120 420 L${off(1120, 420)} L${off(1120, 448)} L1120 448 Z`} fill="#161b21" stroke="#2f3843" />
      {Array.from({ length: 12 }, (_, i) => (
        <circle key={i} cx={610 + i * 42} cy="434" r="2" fill="#45505d" />
      ))}
      {/* fifth wheel coupling */}
      <path d={`M846 408 H968 L${off(968, 408)} L${off(846, 408)} Z`} fill="#20262e" stroke="#3a4450" />
      <path d="M846 408 H968 V420 H846 Z" fill="#262d36" stroke="#3a4450" />
      {/* drive line (motion zone draws the highlight) */}
      <path d={`M560 470 L${AX.a2 - 20} ${WY - 4}`} stroke="#39434f" strokeWidth="6" strokeLinecap="round" />

      {/* mudguards over the rear axles */}
      <path d={`M${AX.a2 - 78} 450 Q ${AX.a2 - 72} 398 ${AX.a2} 398 L ${AX.a3} 398 Q ${AX.a3 + 72} 398 ${AX.a3 + 78} 450`}
        fill="none" stroke="#39434f" strokeWidth="9" strokeLinecap="round" />

      {/* near wheels */}
      <Wheel cx={AX.a2} cy={WY} />
      <Wheel cx={AX.a3} cy={WY} />

      {/* air tanks + battery box behind the cab */}
      <rect x="784" y="440" width="54" height="30" rx="4" fill="#232a33" stroke="#3a4450" />
      <rect x="590" y="408" width="40" height="12" rx="3" fill="#2a323c" stroke="#3a4450" />
      <path d="M592 408 V376 H606" fill="none" stroke="#39434f" strokeWidth="5" strokeLinecap="round" />
      {/* fuel tank (near side, between axles) */}
      <g>
        <rect x="604" y="438" width="176" height="66" rx="22" fill="url(#tank)" stroke="#46515e" strokeWidth="1.2" />
        <ellipse cx="626" cy="471" rx="14" ry="31" fill="#2b333d" stroke="#4d5866" strokeWidth="1" />
        <line x1="648" x2="760" y1="452" y2="452" stroke="rgba(255,255,255,0.08)" strokeWidth="3" strokeLinecap="round" />
        <rect x="700" y="432" width="16" height="8" rx="2" fill="#39434f" />
        <path d="M628 504 V512 M760 504 V512" stroke="#39434f" strokeWidth="3" />
      </g>

      {/* ---- cab ---- */}
      {/* roof + air deflector */}
      <path d={`M${x0} ${top} H${x1} L${off(x1, top)} L${off(x0, top)} Z`} fill="url(#roof)" stroke="#3a4450" />
      <path d={`M${x0 + 118} ${top} C ${x0 + 150} ${top - 40} ${x0 + 196} ${top - 56} ${x1 - 6} ${top - 58} L ${x1} ${top - 56} V${top} Z`}
        fill="url(#cabSide)" stroke="#3a4450" />
      <path d={`M${x0 + 124} ${top - 2} C ${x0 + 156} ${top - 40} ${x0 + 200} ${top - 54} ${x1 - 8} ${top - 56}`} fill="none" stroke="rgba(255,255,255,0.10)" strokeWidth="1.5" />
      {/* front face */}
      <path d={`M${x0} ${top} L${x0} ${bot} L${off(x0, bot)} L${off(x0, top)} Z`} fill="url(#cabFront)" stroke="#3a4450" />
      {/* sun visor */}
      <path d={`M${x0 + 2} ${top + 4} L${x0 + 2} ${top + 16} L${off(x0 + 2, top + 16)} L${off(x0 + 2, top + 4)} Z`} fill="#141a20" />
      {/* windscreen */}
      <path d={`M${x0 - 4} ${top + 22} L${x0 - 4} ${top + 128} L${off(x0 + 8, top + 128)} L${off(x0 + 8, top + 22)} Z`}
        fill="url(#glass)" stroke="#4b5764" strokeWidth="1" />
      <path d={`M${x0 - 16} ${top + 40} L${off(x0 + 20, top + 110)}`} stroke="rgba(255,255,255,0.10)" strokeWidth="10" strokeLinecap="round" />
      {/* grille */}
      <path d={`M${x0 - 4} ${top + 150} L${x0 - 4} ${top + 238} L${off(x0 + 8, top + 238)} L${off(x0 + 8, top + 150)} Z`}
        fill="#0f1317" stroke="#3e4955" />
      {Array.from({ length: 7 }, (_, i) => {
        const y = top + 160 + i * 11.5;
        return <path key={i} d={`M${x0 - 8} ${y} L${off(x0 + 4, y)}`} stroke="#39434f" strokeWidth="3" strokeLinecap="round" />;
      })}
      {/* headlights */}
      <path d={`M${x0 - 6} ${bot - 58} L${x0 - 6} ${bot - 40} L${x0 - 34} ${bot - 50} L${x0 - 34} ${bot - 68} Z`} fill="url(#lamp)" stroke="#5a6674" />
      <path d={`M${x0 + D.x + 36} ${bot + D.y - 58} L${x0 + D.x + 36} ${bot + D.y - 40} L${x0 + D.x + 8} ${bot + D.y - 50} L${x0 + D.x + 8} ${bot + D.y - 68} Z`}
        fill="url(#lamp)" stroke="#5a6674" opacity="0.85" />
      {/* bumper */}
      <path d={`M${x0} ${bot - 30} L${x0} ${bot} L${off(x0, bot)} L${off(x0, bot - 30)} Z`} fill="#161b21" stroke="#39434f" />

      {/* cab near side */}
      <path d={`M${x0} ${top + 10} Q ${x0} ${top} ${x0 + 10} ${top} H${x1} V${bot} H${x0} Z`} fill="url(#cabSide)" stroke="#3f4a57" strokeWidth="1.2" />
      {/* door */}
      <rect x={x0 + 26} y={top + 26} width="180" height="276" rx="10" fill="none" stroke="#3a4450" strokeWidth="1.4" />
      <path d={`M${x0 + 36} ${top + 36} H${x0 + 196} V${top + 138} H${x0 + 36} Z`} fill="url(#glass)" stroke="#4b5764" />
      <path d={`M${x0 + 44} ${top + 130} L${x0 + 110} ${top + 44}`} stroke="rgba(255,255,255,0.08)" strokeWidth="14" strokeLinecap="round" />
      <rect x={x0 + 168} y={top + 176} width="22" height="6" rx="3" fill="#56616f" />
      {/* body crease + side skirt */}
      <path d={`M${x0 + 2} ${top + 150} H${x1 - 2}`} stroke="#39434f" strokeWidth="1.2" />
      <path d={`M${x0 + 2} ${top + 152} H${x1 - 2}`} stroke="rgba(255,255,255,0.05)" strokeWidth="1" />
      <path d={`M${AX.a1 + 86} ${bot - 30} H${x1} V${bot} H${AX.a1 + 86} Z`} fill="#1b2128" stroke="#343e49" />
      {/* rear cab panel grooves */}
      {[0, 1, 2].map((i) => (
        <line key={i} x1={x0 + 232 + i * 18} x2={x0 + 232 + i * 18} y1={top + 40} y2={bot - 40} stroke="#2a323c" strokeWidth="2" />
      ))}
      {/* front wheel arch + steps */}
      <path d={`M${AX.a1 - 82} ${bot} Q ${AX.a1 - 76} ${WY - 80} ${AX.a1} ${WY - 80} Q ${AX.a1 + 76} ${WY - 80} ${AX.a1 + 82} ${bot}`}
        fill="#0b0e12" stroke="#3f4a57" strokeWidth="1.2" />
      {[0, 1].map((i) => <rect key={i} x={x0 + 150} y={bot - 70 + i * 34} width="70" height="8" rx="2" fill="#2f3843" />)}
      {/* mirror */}
      <path d={`M${x0} ${top + 42} L${x0 - 26} ${top + 34}`} stroke="#4a5563" strokeWidth="4" strokeLinecap="round" />
      <rect x={x0 - 44} y={top + 14} width="22" height="66" rx="5" fill="#1a2027" stroke="#56616f" />
      {/* cab edge highlight */}
      <path d={`M${x0 + 10} ${top + 1} H${x1 - 2}`} stroke="rgba(255,255,255,0.14)" strokeWidth="1.5" />
      <path d={`M${x0 + 1} ${top + 10} V${bot - 2}`} stroke="rgba(255,255,255,0.08)" strokeWidth="1.5" />

      {/* near front wheel */}
      <Wheel cx={AX.a1} cy={WY} />
    </g>
  );
}

// ---- system zones (hit area + highlight) ------------------------------------------------------
function zoneShapes(id: VehicleSystemId): ReactNode {
  const { x0, top } = CAB;
  switch (id) {
    case "engine": // x-ray: engine block under the cab
      return (
        <>
          <path d={`M312 318 H534 V424 H312 Z`} />
          <path d="M324 300 H514 V318 H324 Z" />
          {[0, 1, 2, 3, 4, 5].map((i) => <path key={i} d={`M${334 + i * 30} 300 V292 H${354 + i * 30} V300`} />)}
          <path d="M534 346 H566 V410 H534" />
        </>
      );
    case "cooling": // grille + radiator core behind it
      return (
        <>
          <path d={`M${x0 - 4} ${top + 150} L${x0 - 4} ${top + 238} L${off(x0 + 8, top + 238)} L${off(x0 + 8, top + 150)} Z`} />
          <path d={`M${x0 + 4} ${top + 150} H${x0 + 24} V${top + 290} H${x0 + 4} Z`} />
        </>
      );
    case "driver": // cab interior: windscreen, side window, steering wheel
      return (
        <>
          <path d={`M${x0 - 4} ${top + 22} L${x0 - 4} ${top + 128} L${off(x0 + 8, top + 128)} L${off(x0 + 8, top + 22)} Z`} />
          <path d={`M${x0 + 36} ${top + 36} H${x0 + 196} V${top + 138} H${x0 + 36} Z`} />
          <ellipse cx={x0 + 72} cy={top + 118} rx="26" ry="9" transform={`rotate(-58 ${x0 + 72} ${top + 118})`} />
          <path d={`M${x0 + 120} ${top + 96} h46 v70 h-46 Z`} />
        </>
      );
    case "fuel":
      return <rect x="604" y="438" width="176" height="66" rx="22" />;
    case "motion":
      return (
        <>
          {[AX.a1, AX.a2, AX.a3].map((x) => <circle key={x} cx={x} cy={WY} r={R + 3} />)}
          <path d={`M560 466 L${AX.a2 - 20} ${WY - 8} L${AX.a2 - 18} ${WY + 2} L560 476 Z`} />
        </>
      );
    case "environment": // air around the vehicle (sensor location not specified by the data)
      return (
        <>
          <circle cx="128" cy="104" r="26" />
          <path d="M72 150 q 30 -14 60 0 t 60 0" fill="none" />
          <path d="M60 178 q 30 -14 60 0 t 60 0" fill="none" />
        </>
      );
  }
}

// ---- callout chip ------------------------------------------------------------------------------
function Chip({ id, label, ann, state }: { id: VehicleSystemId; label: string; ann?: SignalAnnotation; state: "idle" | "hover" | "active" | "highlight" }) {
  const c = CALLOUTS[id];
  const [cx, cy] = c.chip;
  const [hx, hy] = c.hot;
  const anchorY = cy > hy ? cy : cy + CHIP_H;
  const anchorX = Math.min(Math.max(hx, cx + 18), cx + CHIP_W - 18);
  const on = state !== "idle";
  const stroke = state === "highlight" ? color.attention : on ? color.accent : color.borderStrong;
  const q = ann?.quality;
  return (
    <g className={`vchip ${state}`} pointerEvents="none">
      <polyline points={`${hx},${hy} ${hx},${(hy + anchorY) / 2} ${anchorX},${(hy + anchorY) / 2} ${anchorX},${anchorY}`}
        fill="none" stroke={stroke} strokeWidth={on ? 1.4 : 1} strokeDasharray={on ? undefined : "3 4"} opacity={on ? 0.95 : 0.55} />
      <rect x={cx} y={cy} width={CHIP_W} height={CHIP_H} rx="8" fill="rgba(14,18,22,0.92)" stroke={stroke} strokeWidth={on ? 1.4 : 1} />
      <text x={cx + 14} y={cy + 20} fill={on ? color.text : color.text2} fontSize="11" fontFamily="IBM Plex Mono" letterSpacing="1.4">
        {label.toUpperCase()}
      </text>
      {ann ? (
        <>
          <text x={cx + 14} y={cy + 42} fill={color.text} fontSize="14.5" fontFamily="IBM Plex Sans" fontWeight="500">
            {ann.label}{ann.value !== undefined ? ": " : ""}
            {ann.value !== undefined && <tspan fontFamily="IBM Plex Mono" fill={q === "warning" ? color.attention : color.text}>{ann.value}{ann.unit ? ` ${ann.unit}` : ""}</tspan>}
          </text>
          <text x={cx + 14} y={cy + 60} fill={color.muted} fontSize="10" fontFamily="IBM Plex Mono" letterSpacing="1">
            {q === "warning" ? "⚠ " : q === "unavailable" ? "⊘ " : ""}
            {(ann.context.label ?? CONTEXT_LABEL[ann.context.kind]).toUpperCase()} · {PROVENANCE[ann.provenance].label.toUpperCase()}
          </text>
        </>
      ) : (
        <text x={cx + 14} y={cy + 44} fill={color.muted} fontSize="12" fontFamily="IBM Plex Sans">Select to inspect signals</text>
      )}
    </g>
  );
}

// ---- renderer -----------------------------------------------------------------------------------
export default function SvgTruckRenderer(props: VehicleRendererProps) {
  const uid = useId().replace(/:/g, "");
  const { systems, hoveredSystem, activeSystem, highlightedSystems = [], annotations = [], variant } = props;
  const showChips = variant !== "compact";
  // when a page supplies annotations, only annotated systems get a callout (less clutter)
  const chipFor = (id: VehicleSystemId) => showChips && (annotations.length === 0 || !!annFor(id) || activeSystem === id || hoveredSystem === id);
  const annFor = (id: VehicleSystemId) => annotations.find((a) => systemOf(a.signal) === id);
  const stateOf = (id: VehicleSystemId): "idle" | "hover" | "active" | "highlight" =>
    activeSystem === id ? "active" : hoveredSystem === id ? "hover" : highlightedSystems.includes(id) ? "highlight" : "idle";
  const select = (id: VehicleSystemId) => props.onSystemSelect?.(activeSystem === id ? null : id);
  const onKey = (id: VehicleSystemId) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      select(id);
    }
  };
  const viewBox = variant === "compact" ? `0 60 ${W} ${H - 140}` : `0 0 ${W} ${H}`;

  return (
    <svg className={`truck truck-${variant}`} viewBox={viewBox} role="group"
      aria-label={props.caption ?? "Generic heavy-duty tractor with selectable vehicle systems"}>
      <defs>
        <pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse">
          <circle cx="1" cy="1" r="1" fill="#1d242c" />
        </pattern>
        <filter id="blur" x="-10%" y="-200%" width="120%" height="500%"><feGaussianBlur stdDeviation="10" /></filter>
        <linearGradient id="cabSide" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#323b46" /><stop offset="0.55" stopColor="#252c35" /><stop offset="1" stopColor="#1a2027" />
        </linearGradient>
        <linearGradient id="cabFront" x1="1" y1="0" x2="0" y2="0">
          <stop offset="0" stopColor="#262e37" /><stop offset="1" stopColor="#1b2128" />
        </linearGradient>
        <linearGradient id="roof" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor="#3a4450" /><stop offset="1" stopColor="#2c343e" />
        </linearGradient>
        <linearGradient id="glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#1d2a36" /><stop offset="0.5" stopColor="#101820" /><stop offset="1" stopColor="#16222c" />
        </linearGradient>
        <linearGradient id="tank" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#56616e" /><stop offset="0.35" stopColor="#3a434e" /><stop offset="1" stopColor="#1f252c" />
        </linearGradient>
        <linearGradient id="rail" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#2c343e" /><stop offset="1" stopColor="#171c22" />
        </linearGradient>
        <radialGradient id="tyre" cx="0.45" cy="0.4" r="0.7">
          <stop offset="0" stopColor="#1b2027" /><stop offset="1" stopColor="#0a0c0f" />
        </radialGradient>
        <radialGradient id="rim" cx="0.4" cy="0.35" r="0.8">
          <stop offset="0" stopColor="#4d5865" /><stop offset="1" stopColor="#262d35" />
        </radialGradient>
        <linearGradient id="lamp" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#c9d4de" /><stop offset="1" stopColor="#5d6a78" />
        </linearGradient>
      </defs>

      <Backdrop />
      <Body />

      {/* interactive system zones */}
      {systems.map((s) => {
        const st = stateOf(s.id);
        return (
          <g key={s.id} className={`vz vz-${s.id} ${st}`} role="button" tabIndex={0} aria-pressed={activeSystem === s.id}
            aria-label={`${s.label}: ${s.signals.length} decoded signal${s.signals.length > 1 ? "s" : ""}`}
            onMouseEnter={() => props.onSystemHover(s.id)} onMouseLeave={() => props.onSystemHover(null)}
            onFocus={() => props.onSystemHover(s.id)} onBlur={() => props.onSystemHover(null)}
            onClick={() => select(s.id)} onKeyDown={onKey(s.id)}>
            <title>{s.label}</title>
            {zoneShapes(s.id)}
          </g>
        );
      })}

      {/* hotspot markers + callouts */}
      {systems.map((s) => {
        const st = stateOf(s.id);
        const [hx, hy] = CALLOUTS[s.id].hot;
        return (
          <g key={`m-${s.id}`} pointerEvents="none">
            {chipFor(s.id) && <Chip id={s.id} label={s.label} ann={annFor(s.id)} state={st} />}
            <circle cx={hx} cy={hy} r="9" className={`vmark ${st}`} />
            <circle cx={hx} cy={hy} r="3.5" fill={st === "highlight" ? color.attention : st === "idle" ? color.text2 : color.accent} />
            {st === "active" && <circle cx={hx} cy={hy} r="9" className="vpulse" />}
          </g>
        );
      })}
      <desc id={`truck-desc-${uid}`}>Generic European heavy-duty tractor illustration. Not an exact model of the recorded vehicle.</desc>
    </svg>
  );
}
