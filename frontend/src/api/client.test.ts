import { afterEach, describe, expect, it, vi } from "vitest";
import { api, post } from "./client";
afterEach(() => vi.unstubAllGlobals());
describe("API boundary", () => {
  it("keeps credentials in cookies and forwards the CSRF token", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ csrf_token: "test-token" }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: 1 }) });
    vi.stubGlobal("fetch", fetch);
    await api("/api/auth/session");
    await post("/api/example", { id: 1 });
    expect(fetch.mock.calls[1][1].credentials).toBe("include");
    expect(fetch.mock.calls[1][1].headers["X-CSRF-Token"]).toBe("test-token");
  });
  it("never reflects server error bodies that may contain secrets", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({ detail: "private-secret" }),
      }),
    );
    await expect(api("/api/example")).rejects.toThrow("500");
    await expect(api("/api/example")).rejects.not.toThrow("private-secret");
  });
});
