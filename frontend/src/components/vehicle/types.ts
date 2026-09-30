/**
 * Vehicle visualization contract (renderer-agnostic).
 *
 * Pages (Overview, Digital Twin, Event Detail, Sensor Explorer) talk only to this interface.
 * A renderer (SVG today, possibly 3D later) implements `VehicleRendererProps` and is registered
 * in VehicleVisualization.tsx - swapping renderers never touches the pages.
 */
import type { ProvenanceKind } from "../foundation/Provenance";

export type VehicleSystemId = "engine" | "fuel" | "cooling" | "driver" | "motion" | "environment";

/**
 * Silver telemetry signal names decoded by FleetPulse V1 (see docs/signal_definitions.md).
 * Values are never invented: annotations carry values supplied by the API.
 */
export type DecodedSignalName =
  | "engine_speed_rpm"
  | "engine_load_pct"
  | "accelerator_pedal_pct"
  | "wheel_speed_kph"
  | "cruise_control_active"
  | "brake_switch"
  | "clutch_switch"
  | "fuel_rate_lph"
  | "total_fuel_used_l"
  | "total_distance_m"
  | "coolant_temp_c"
  | "ambient_temp_c";

export interface VehicleSystem {
  id: VehicleSystemId;
  label: string;
  /** what the system region represents on the vehicle */
  description: string;
  /** decoded signals presented under this system (presentation grouping only) */
  signals: DecodedSignalName[];
}

/** Presentation taxonomy: which decoded signals are shown under which vehicle system. */
export const VEHICLE_SYSTEMS: readonly VehicleSystem[] = [
  { id: "engine", label: "Engine / Powertrain", description: "Engine speed and load at current speed.",
    signals: ["engine_speed_rpm", "engine_load_pct"] },
  { id: "fuel", label: "Fuel System", description: "Fuel rate indication and the high-resolution fuel counter.",
    signals: ["fuel_rate_lph", "total_fuel_used_l"] },
  { id: "cooling", label: "Cooling", description: "Engine coolant temperature.",
    signals: ["coolant_temp_c"] },
  { id: "driver", label: "Driver Controls", description: "Accelerator pedal, service brake, cruise control and clutch switches.",
    signals: ["accelerator_pedal_pct", "brake_switch", "cruise_control_active", "clutch_switch"] },
  { id: "motion", label: "Vehicle Motion / Wheels", description: "Wheel-based vehicle speed and the total distance counter.",
    signals: ["wheel_speed_kph", "total_distance_m"] },
  { id: "environment", label: "Environment", description: "Ambient air temperature (known data-quality concerns in this dataset).",
    signals: ["ambient_temp_c"] },
] as const;

export const systemOf = (signal: DecodedSignalName): VehicleSystemId | undefined =>
  VEHICLE_SYSTEMS.find((s) => s.signals.includes(signal))?.id;

/** Where an annotated value comes from. Nothing on a vehicle view is ever presented as live. */
export type AnnotationContext =
  | { kind: "reference_event"; eventId: string; label?: string }
  | { kind: "session"; sessionId: string; label?: string }
  | { kind: "dataset"; label?: string }
  | { kind: "catalog"; label?: string }; // signal identity only, no value

/** A value or status shown next to a vehicle system (e.g. "REFERENCE EVENT · Engine speed 600.1 rpm · MEASURED"). */
export interface SignalAnnotation {
  signal: DecodedSignalName;
  label: string;
  value?: string; // formatted display value supplied by the page from API data
  unit?: string;
  context: AnnotationContext;
  provenance: ProvenanceKind;
  /** data-quality flag to render with an icon + text (never colour alone) */
  quality?: "ok" | "warning" | "unavailable";
  qualityNote?: string;
}

export type VehicleRendererId = "svg" | "3d";

export interface VehicleVisualizationProps {
  renderer?: VehicleRendererId;
  /** system opened in a detail view (selected) */
  activeSystem?: VehicleSystemId | null;
  /** systems emphasised without being selected (e.g. systems involved in an event) */
  highlightedSystems?: VehicleSystemId[];
  /** values/statuses attached to systems; rendered with connector lines by capable renderers */
  annotations?: SignalAnnotation[];
  onSystemSelect?: (system: VehicleSystemId | null) => void;
  /** layout density: overview hero, full inspection, or compact event context */
  variant?: "hero" | "inspect" | "compact";
  /** accessible description of what the view shows */
  caption?: string;
}

/** Props every renderer receives (renderer id resolved, defaults applied). */
export type VehicleRendererProps = Required<Pick<VehicleVisualizationProps, "variant">> &
  Omit<VehicleVisualizationProps, "renderer" | "variant"> & {
    systems: readonly VehicleSystem[];
    hoveredSystem: VehicleSystemId | null;
    onSystemHover: (system: VehicleSystemId | null) => void;
  };
