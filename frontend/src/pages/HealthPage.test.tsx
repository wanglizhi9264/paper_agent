import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HealthPage } from "./HealthPage";
import * as healthFeature from "../features/health";

function withProviders(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={node} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("HealthPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows loading state initially", () => {
    vi.spyOn(healthFeature, "useReadyHealth").mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof healthFeature.useReadyHealth>);

    render(withProviders(<HealthPage />));
    expect(screen.getByText(/Checking service status/i)).toBeInTheDocument();
  });

  it("renders overall and component statuses", () => {
    vi.spyOn(healthFeature, "useReadyHealth").mockReturnValue({
      data: {
        status: "ok",
        components: {
          postgres: { status: "ok" },
          redis: { status: "ok" },
          index: { status: "ok", detail: "not_initialized" },
        },
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof healthFeature.useReadyHealth>);

    render(withProviders(<HealthPage />));
    expect(screen.getByText("Overall")).toBeInTheDocument();
    expect(screen.getByText("postgres")).toBeInTheDocument();
    expect(screen.getByText("not_initialized")).toBeInTheDocument();
  });

  it("renders error state with retry", () => {
    vi.spyOn(healthFeature, "useReadyHealth").mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("boom"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof healthFeature.useReadyHealth>);

    render(withProviders(<HealthPage />));
    expect(screen.getByText(/Could not reach the backend/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/i })).toBeInTheDocument();
  });
});
