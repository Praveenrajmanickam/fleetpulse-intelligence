import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ApiUnavailable, QueryError } from "../components/foundation/ApiState";
import { PROVENANCE, Provenance, ProvenanceLegend, type ProvenanceKind } from "../components/foundation/Provenance";
import { Kpi } from "../components/ui";
import { ApiError } from "../lib/api";

describe("ApiUnavailable", () => {
  it("explains the state in product terms and offers a retry", async () => {
    const onRetry = vi.fn();
    render(<ApiUnavailable onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("FleetPulse UI is running")).toBeInTheDocument();
    expect(screen.getAllByText("Backend API unavailable").length).toBeGreaterThan(0);
    expect(screen.getByText("Operational intelligence could not be loaded")).toBeInTheDocument();
    expect(screen.queryByText(/Internal Server Error/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry connection" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe("QueryError", () => {
  it("routes an unavailable backend to the connection state", () => {
    render(<QueryError error={new ApiError(500, "Internal Server Error", "unavailable")} />);
    expect(screen.getByText("FleetPulse UI is running")).toBeInTheDocument();
  });
  it("shows a calm not-found state for 404s", () => {
    render(<QueryError error={new ApiError(404, "idle event 'x' not found", "not_found")} />);
    expect(screen.getByText("Not found")).toBeInTheDocument();
    expect(screen.getByText("idle event 'x' not found", { selector: "p" })).toBeInTheDocument();
  });
});

describe("Provenance", () => {
  const kinds: ProvenanceKind[] = ["measured", "derived", "assumed", "ai"];
  it.each(kinds)("%s has a visible label, an accessible name and a data attribute", (kind) => {
    const { container } = render(<Provenance kind={kind} />);
    expect(screen.getByLabelText(`Provenance: ${PROVENANCE[kind].label}`)).toBeInTheDocument();
    expect(screen.getByText(PROVENANCE[kind].label)).toBeInTheDocument();
    expect(container.querySelector(`[data-provenance="${kind}"] svg`)).not.toBeNull(); // shape, not colour only
  });
  it("compact keeps the accessible name without the visible label", () => {
    render(<Provenance kind="assumed" compact />);
    expect(screen.getByLabelText("Provenance: Assumed")).toBeInTheDocument();
    expect(screen.queryByText("Assumed")).not.toBeInTheDocument();
  });
  it("the legend explains all four kinds", () => {
    render(<ProvenanceLegend />);
    for (const k of kinds) expect(screen.getByText(PROVENANCE[k].description)).toBeInTheDocument();
  });
});

describe("Kpi", () => {
  it("shows historical context and provenance, and is keyboard-activatable when linked", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<Kpi label="Engine speed" value="600.1" unit="rpm" context="Reference event" provenance="measured" to="/next" />} />
          <Route path="/next" element={<p>navigated</p>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("Reference event")).toBeInTheDocument();
    expect(screen.getByLabelText("Provenance: Measured")).toBeInTheDocument();
    const link = screen.getByRole("link");
    link.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByText("navigated")).toBeInTheDocument();
  });
});
