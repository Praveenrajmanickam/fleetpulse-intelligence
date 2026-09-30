import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import VehicleVisualization from "../components/vehicle/VehicleVisualization";
import { VEHICLE_SYSTEMS, systemOf, type DecodedSignalName } from "../components/vehicle/types";

// The 12 signals decoded by FleetPulse V1 (docs/signal_definitions.md, Silver telemetry columns).
const DECODED: DecodedSignalName[] = [
  "engine_speed_rpm", "accelerator_pedal_pct", "engine_load_pct", "wheel_speed_kph", "cruise_control_active",
  "brake_switch", "clutch_switch", "fuel_rate_lph", "total_fuel_used_l", "total_distance_m", "coolant_temp_c", "ambient_temp_c",
];

describe("vehicle system taxonomy", () => {
  it("has the six systems", () => {
    expect(VEHICLE_SYSTEMS.map((s) => s.id)).toEqual(["engine", "fuel", "cooling", "driver", "motion", "environment"]);
  });
  it("maps every decoded signal to exactly one system and invents none", () => {
    const all = VEHICLE_SYSTEMS.flatMap((s) => s.signals);
    expect([...all].sort()).toEqual([...DECODED].sort());
    expect(new Set(all).size).toBe(all.length);
    expect(systemOf("ambient_temp_c")).toBe("environment");
    expect(systemOf("engine_speed_rpm")).toBe("engine");
  });
});

describe("VehicleVisualization (SVG renderer)", () => {
  it("falls back to the SVG renderer and exposes six keyboard-operable systems", async () => {
    const onSelect = vi.fn();
    render(<VehicleVisualization renderer="3d" onSystemSelect={onSelect} caption="Truck" />);
    const engine = await screen.findByRole("button", { name: /Engine \/ Powertrain/ });
    expect(screen.getAllByRole("button")).toHaveLength(6);
    engine.focus();
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith("engine");
  });

  it("marks the active system as pressed and shows annotated values with their context", async () => {
    render(
      <VehicleVisualization activeSystem="fuel" variant="hero" caption="Truck"
        annotations={[{ signal: "engine_speed_rpm", label: "Avg engine speed", value: "600.1", unit: "rpm",
          context: { kind: "reference_event", eventId: "e1" }, provenance: "measured" }]} />,
    );
    const fuel = await screen.findByRole("button", { name: /Fuel System/ });
    expect(fuel).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/REFERENCE EVENT · MEASURED/)).toBeInTheDocument();
    expect(screen.queryByText(/live/i)).not.toBeInTheDocument();
  });
});
