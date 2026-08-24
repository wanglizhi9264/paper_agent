import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPostForm, apiPost } from "../api/client";
import type { DocumentListResponse, CreateDocumentResponse } from "../types/document";

export function useDocuments() {
  return useQuery<DocumentListResponse>({
    queryKey: ["documents"],
    queryFn: () => apiGet<DocumentListResponse>("/api/v1/documents?limit=50"),
    retry: 1,
    initialData: { items: [], next_cursor: null },
    initialDataUpdatedAt: 0,
    staleTime: 0,
    gcTime: 0,
    refetchInterval: (query) => {
      const data = query.state.data as DocumentListResponse | undefined;
      if (!data) return 5_000;
      const hasActive = data.items.some(
        (d) => d.status === "queued" || d.status === "parsing" || d.status === "chunking" || d.status === "embedding" || d.status === "indexing",
      );
      return hasActive ? 3_000 : 30_000;
    },
    refetchOnWindowFocus: true,
  });
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation<
    CreateDocumentResponse,
    Error,
    { file: File; collectionIds?: string[] }
  >({
    mutationFn: ({ file, collectionIds }) => {
      const fd = new FormData();
      fd.append("file", file);
      if (collectionIds) {
        for (const id of collectionIds) {
          fd.append("collection_ids", id);
        }
      }
      return apiPostForm<CreateDocumentResponse>("/api/v1/documents", fd);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export function useReindexDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (docId: string) =>
      apiPost<{ job_id: string }>(`/api/v1/documents/${docId}/reindex`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (docId: string) =>
      apiPost<{ job_id: string }>(`/api/v1/documents/${docId}/delete`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}
