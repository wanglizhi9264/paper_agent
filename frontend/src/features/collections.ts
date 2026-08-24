import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost, apiDelete } from "../api/client";
import type { CollectionListResponse, CollectionItem } from "../types/collection";

export function useCollections() {
  return useQuery<CollectionListResponse>({
    queryKey: ["collections"],
    queryFn: () => apiGet<CollectionListResponse>("/api/v1/collections?limit=50"),
    retry: 1,
    initialData: { items: [], next_cursor: null },
    initialDataUpdatedAt: 0,
    staleTime: 10_000,
    gcTime: 0,
  } as never);
}

export function useCreateCollection() {
  const qc = useQueryClient();
  return useMutation<CollectionItem, Error, { name: string; description?: string }>({
    mutationFn: (body) =>
      apiPost<CollectionItem>("/api/v1/collections", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["collections"] });
    },
  });
}

export function useDeleteCollection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiDelete<void>(`/api/v1/collections/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["collections"] });
    },
  });
}
