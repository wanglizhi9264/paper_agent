import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../api/client";
import type { LiveResponse, ReadyResponse } from "../types/health";

export function useLiveHealth() {
  return useQuery<LiveResponse>({
    queryKey: ["health", "live"],
    queryFn: () => apiGet<LiveResponse>("/health/live"),
    refetchInterval: 30_000,
    retry: 1,
  });
}

export function useReadyHealth() {
  return useQuery<ReadyResponse>({
    queryKey: ["health", "ready"],
    queryFn: () => apiGet<ReadyResponse>("/health/ready"),
    refetchInterval: 30_000,
    retry: 1,
  });
}
