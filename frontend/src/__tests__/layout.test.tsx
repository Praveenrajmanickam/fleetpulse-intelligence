import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Layout from "../components/Layout";

const META = {
  build: { gold_build_id: "20260930T071045Z-0ae17fad", built_at_utc: "2026-09-30T07:10:45Z", rule_version: "idle-v1", silver_build_ids: ["s"], rule_config: {} },
  scope: { vehicles: 1, vehicle_id: "turku-renault-t520-6x2", observation_window: "Part 3", statement: "", period_start: "2021-01-18T12:35:38", period_end: "2021-02-10T11:48:15", timestamp_timezone: "unknown", gold_scope: "" },
  module_statement: "", modules: [],
};

function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<p>page content</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Layout", () => {
  it("replaces the page with the connection state when the API is down, and retries", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    renderShell();
    expect(await screen.findByText("FleetPulse UI is running")).toBeInTheDocument();
    expect(screen.queryByText("page content")).not.toBeInTheDocument();
    expect(screen.getByText("API unavailable")).toBeInTheDocument();
    fetch.mockResolvedValue(new Response(JSON.stringify(META), { status: 200, headers: { "content-type": "application/json" } }));
    await userEvent.click(screen.getByRole("button", { name: "Retry connection" }));
    expect(await screen.findByText("page content")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("API connected")).toBeInTheDocument());
  });

  it("marks unimplemented modules as disabled, never as links", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(META), { status: 200 }));
    renderShell();
    await screen.findByText("API connected");
    for (const label of ["Fuel Intelligence", "Engine Intelligence"]) {
      expect(screen.queryByRole("link", { name: new RegExp(label) })).not.toBeInTheDocument();
      expect(screen.getByText(label).closest("[aria-disabled='true']")).not.toBeNull();
    }
    for (const label of ["Overview", "Vehicle Digital Twin", "Sensor Explorer", "Signal Health", "CAN → Gold Trace",
      "Idle Intelligence", "Event Explorer", "Impact Scenario", "Session Explorer"]) {
      expect(screen.getByRole("link", { name: new RegExp(`^${label}`) })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: /Skip to content/ })).toHaveAttribute("href", "#main");
  });

  it("labels the data as historical, never live", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(META), { status: 200 }));
    renderShell();
    await screen.findByText("API connected");
    expect(screen.getByText(/^Historical recording/)).toBeInTheDocument();
    expect(screen.queryByText(/\blive\b/i)).not.toBeInTheDocument();
  });
});
