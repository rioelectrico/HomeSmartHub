import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import nextConfig from "../next.config";
import { ApiError, apiFetch, resolveApiUrl } from "@/lib/api";

describe("browser API transport", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("uses the same-origin proxy unless a public API origin was explicitly configured", () => {
    expect(resolveApiUrl("/api/auth/me")).toBe("/api/auth/me");
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.example.test/");
    expect(resolveApiUrl("/api/auth/me")).toBe("https://api.example.test/api/auth/me");
  });

  it("forwards API paths through the server-side backend origin", async () => {
    vi.stubEnv("BACKEND_ORIGIN", "http://backend.internal:8000/");
    expect(await nextConfig.rewrites?.()).toEqual([
      { source: "/api/:path*", destination: "http://backend.internal:8000/api/:path*" },
    ]);
  });

  it("announces a redirect only for an unauthorized API response", async () => {
    const unauthorized = vi.fn();
    window.addEventListener("portero:unauthorized", unauthorized);
    vi.mocked(fetch).mockResolvedValue(Response.json({ error: { code: "INVALID_CREDENTIALS" } }, { status: 401 }));

    await expect(apiFetch("/api/auth/me")).rejects.toEqual(expect.any(ApiError));
    expect(unauthorized).toHaveBeenCalledTimes(1);
    window.removeEventListener("portero:unauthorized", unauthorized);
  });
});
