/** V2 pages rendered against real API responses captured from the Part 3 build (fixtures/). */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AnalystPanel from "../components/AnalystPanel";
import SensorExplorer from "../pages/SensorExplorer";
import SignalHealth from "../pages/SignalHealth";
import Trace from "../pages/Trace";
import aiStatus from "./fixtures/ai_status.json";
import aiWaste from "./fixtures/ai_waste.json";
import health from "./fixtures/health.json";
import signals from "./fixtures/signals.json";
import trace from "./fixtures/trace_engine_speed.json";

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/signals/health")) return json(health);
    if (url.includes("/trace")) return json(trace);
    if (url.endsWith("/signals")) return json(signals);
    if (url.includes("/ai/status")) return json(aiStatus);
    if (url.includes("/ai/ask")) return json(aiWaste);
    return new Response("{}", { status: 404 });
  });
}

const at = (path: string, route: string, el: React.ReactElement) =>
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={el} /></Routes></MemoryRouter>);

beforeEach(() => {
  mockApi();
});

describe("Sensor Explorer", () => {
  it("shows verified J1939 metadata for the selected signal", async () => {
    at("/vehicle/sensors?signal=engine_speed_rpm", "/vehicle/sensors", <SensorExplorer />);
    const detail = (await screen.findByRole("heading", { name: "Engine speed", level: 2 })).closest("section")!;
    const d = within(detail);
    expect(d.getByText("PGN 61444")).toBeInTheDocument();
    expect(d.getByText("SPN 190")).toBeInTheDocument();
    expect(d.getByText("0.125 rpm/bit")).toBeInTheDocument();
    expect(d.getByText(/0xF004 · EEC1 — Electronic Engine Controller 1/)).toBeInTheDocument();
    expect(d.getByText(/engine speed ≥ 300 rpm/)).toBeInTheDocument();
    expect(d.getByRole("link", { name: /Trace signal/ })).toHaveAttribute("href", "/vehicle/trace?signal=engine_speed_rpm");
  });

  it("clicking a vehicle region selects that system's signal", async () => {
    at("/vehicle/sensors?signal=engine_speed_rpm", "/vehicle/sensors", <SensorExplorer />);
    await userEvent.click(await screen.findByRole("button", { name: /^Fuel System/ }));
    expect(await screen.findByRole("heading", { name: "Fuel rate", level: 2 })).toBeInTheDocument();
  });

  it("warns about ambient temperature data quality", async () => {
    at("/vehicle/sensors?signal=ambient_temp_c", "/vehicle/sensors", <SensorExplorer />);
    const detail = (await screen.findByRole("heading", { name: "Ambient air temperature", level: 2 })).closest("section")!;
    expect(within(detail).getAllByText(/Quality warning/).length).toBeGreaterThan(0);
    expect(within(detail).getByText(/heat soak/)).toBeInTheDocument();
  });
});

describe("Signal Health", () => {
  it("is explicitly data health, scoped and historical, never mechanical health", async () => {
    at("/vehicle/signal-health", "/vehicle/signal-health", <SignalHealth />);
    expect(await screen.findByText("Not mechanical vehicle health.")).toBeInTheDocument();
    expect(screen.getByText("803,880")).toBeInTheDocument();
    expect(screen.getByText("Historical recording · not live")).toBeInTheDocument();
    expect(screen.queryByText(/\bhealthy\b/i)).not.toBeInTheDocument();
    const clutch = screen.getByRole("link", { name: "Clutch switch" }).closest(".health-row")!;
    expect(within(clutch as HTMLElement).getAllByText(/Unavailable/).length).toBeGreaterThan(0);
    const amb = screen.getByRole("link", { name: "Ambient air temperature" }).closest(".health-row")!;
    expect(amb).toHaveClass("quality_warning");
  });
});

describe("Traceability", () => {
  it("renders the real frame, decoding and the consistency checks", async () => {
    at("/vehicle/trace?signal=engine_speed_rpm", "/vehicle/trace", <Trace />);
    expect(await screen.findByText(trace.steps.raw_can.can_id_hex)).toBeInTheDocument();
    expect(screen.getByText(/PGN 61444/)).toBeInTheDocument();
    expect(screen.getByText(`${trace.steps.decode.bytes.map((b) => b.hex).join(", ")} → raw ${trace.steps.decode.raw}`)).toBeInTheDocument();
    expect(screen.getByText(/Production decoder = Silver signal event/)).toHaveClass("ok");
    expect(screen.getByText(/Silver telemetry value at the grid row/)).toHaveClass("ok");
    expect(screen.getByText("Operational investigation")).toBeInTheDocument();
  });
});

describe("AI Analyst (mock provider)", () => {
  it("labels mock output and separates measured facts from interpretation", async () => {
    render(<MemoryRouter><AnalystPanel scope="event" eventId="e" inline initialQuestion="Can we call this fuel waste?" /></MemoryRouter>);
    expect(await screen.findByText(/cannot support calling this fuel waste/)).toBeInTheDocument();
    expect(screen.getAllByText(/Development mock · not an LLM/i).length).toBeGreaterThanOrEqual(2);
    for (const t of ["Measured by FleetPulse", "Interpretation", "Limitations", "What to investigate next", "Evidence", "Validation"]) {
      expect(screen.getByText(t)).toBeInTheDocument();
    }
    expect(screen.getByText(/Mock analyst answer · development output, not an LLM/)).toBeInTheDocument();
  });
});
