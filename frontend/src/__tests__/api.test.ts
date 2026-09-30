import { describe, expect, it, vi } from "vitest";
import { ApiError, api, classifyStatus, isUnavailable } from "../lib/api";

describe("API error classification", () => {
  it.each([
    [0, false, "unavailable"],
    [502, false, "unavailable"],
    [503, true, "unavailable"],
    [504, false, "unavailable"],
    [500, false, "unavailable"], // non-JSON 5xx = proxy with the API down (e.g. Vite dev proxy)
    [500, true, "server"], // FastAPI error: the API is up
    [404, true, "not_found"],
    [422, true, "invalid"],
  ] as const)("status %i (json=%s) -> %s", (status, json, kind) => {
    expect(classifyStatus(status, json)).toBe(kind);
  });

  it("a network failure becomes an 'unavailable' ApiError, not a raw TypeError", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await api.meta().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(isUnavailable(err)).toBe(true);
  });

  it("a proxy 500 with a non-JSON body is treated as backend unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 500, statusText: "Internal Server Error" }));
    const err = await api.overview().catch((e) => e);
    expect(isUnavailable(err)).toBe(true);
  });

  it("a FastAPI 404 keeps its detail and is 'not_found'", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "idle event 'x' not found" }), { status: 404, headers: { "content-type": "application/json" } }),
    );
    const err = await api.event("x").catch((e) => e);
    expect(err.kind).toBe("not_found");
    expect(err.message).toContain("not found");
  });
});
