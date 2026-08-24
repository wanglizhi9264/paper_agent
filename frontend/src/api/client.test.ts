import { describe, it, expect, vi, afterEach, type MockedFunction } from "vitest";
import { apiGet } from "./client";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function mockFetch(response: Response): MockedFunction<typeof fetch> {
  const fetchMock = vi.fn(() => Promise.resolve(response)) as unknown as MockedFunction<
    typeof fetch
  >;
  globalThis.fetch = fetchMock;
  return fetchMock;
}

describe("apiGet", () => {
  it("parses a successful JSON response", async () => {
    const resp = new Response(JSON.stringify({ status: "ok" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const fetchMock = mockFetch(resp);

    const result = await apiGet<{ status: string }>("/health/live");
    expect(result).toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledOnce();
    const call = fetchMock.mock.calls[0];
    expect(call).toBeDefined();
    const [url, init] = call ?? ["", undefined];
    expect(String(url)).toContain("/health/live");
    expect(init?.method).toBe("GET");
  });

  it("throws ApiException on error envelope", async () => {
    const body = JSON.stringify({
      error: { code: "INDEX_UNAVAILABLE", message: "no index", details: {}, request_id: "rid-1" },
    });
    const resp = new Response(body, { status: 503, headers: { "content-type": "application/json" } });
    mockFetch(resp);

    await expect(apiGet("/health/ready")).rejects.toMatchObject({
      name: "ApiException",
      code: "INDEX_UNAVAILABLE",
      status: 503,
      requestId: "rid-1",
    });
  });

  it("uses fallback error when body is empty", async () => {
    const resp = new Response("", { status: 500, statusText: "Server Error" });
    mockFetch(resp);

    await expect(apiGet("/health/ready")).rejects.toMatchObject({
      name: "ApiException",
      status: 500,
      code: "UNKNOWN",
    });
  });
});
