import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost, sseStream } from "../api/client";
import type {
  SessionItem,
  SessionListResponse,
  ChatSource,
  ChatCitation,
} from "../types/chat";

export function useSessions() {
  return useQuery<SessionListResponse>({
    queryKey: ["sessions"],
    queryFn: () => apiGet<SessionListResponse>("/api/v1/sessions?limit=50"),
    retry: 1,
    initialData: { items: [], next_cursor: null },
    initialDataUpdatedAt: 0,
    staleTime: 5_000,
    gcTime: 0,
  });
}

export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation<
    SessionItem,
    Error,
    { title: string; scope: { type: string; document_ids?: string[]; collection_id?: string } }
  >({
    mutationFn: (body) => apiPost<SessionItem>("/api/v1/sessions", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
}

export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => {
      const url = `${import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000"}/api/v1/sessions/${id}`;
      return fetch(url, { method: "DELETE" }).then((response) => {
        if (!response.ok) throw new Error(`Request failed (${response.status})`);
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
}

export function useSessionMessages(sessionId: string | null) {
  return useQuery<{ items: Array<{ id: string; role: string; content: string; citations: ChatCitation[] | null; created_at: string }> }>({
    queryKey: ["sessions", sessionId, "messages"],
    queryFn: () =>
      apiGet(`/api/v1/sessions/${sessionId}/messages`),
    enabled: sessionId !== null,
    retry: 1,
    initialData: { items: [] },
    initialDataUpdatedAt: 0,
    staleTime: 0,
    gcTime: 0,
  });
}

export interface SSEState {
  streaming: boolean;
  answer: string;
  sources: ChatSource[];
  citations: ChatCitation[];
  degradedReasons: string[];
  error: string | null;
  requestId: string | null;
}

export function useChatStream() {
  const [state, setState] = useState<SSEState>({
    streaming: false,
    answer: "",
    sources: [],
    citations: [],
    degradedReasons: [],
    error: null,
    requestId: null,
  });

  const start = useCallback(
    async (sessionId: string, query: string) => {
      setState({
        streaming: true,
        answer: "",
        sources: [],
        citations: [],
        degradedReasons: [],
        error: null,
        requestId: null,
      });

      try {
        const gen = sseStream("/api/v1/chat/stream", { session_id: sessionId, query });
        for await (const evt of gen) {
          if (evt.event === "meta") {
            setState((s) => ({
              ...s,
              requestId: (evt.data.request_id as string) ?? null,
            }));
          } else if (evt.event === "sources") {
            const sources = (evt.data.sources as ChatSource[]) ?? [];
            setState((s) => ({ ...s, sources }));
          } else if (evt.event === "delta") {
            const text = (evt.data.text as string) ?? "";
            setState((s) => ({ ...s, answer: s.answer + text }));
          } else if (evt.event === "done") {
            const citations = (evt.data.citations as ChatCitation[]) ?? [];
            const reasons = (evt.data.degraded_reasons as string[]) ?? [];
            setState((s) => ({
              ...s,
              citations,
              degradedReasons: reasons,
              streaming: false,
            }));
            return;
          } else if (evt.event === "error") {
            const errMsg =
              (evt.data.error as { message?: string })?.message ?? "Stream error";
            setState((s) => ({
              ...s,
              error: errMsg,
              streaming: false,
            }));
            return;
          }
        }
      } catch (err) {
        setState((s) => ({
          ...s,
          error: err instanceof Error ? err.message : "Unknown error",
          streaming: false,
        }));
      }
    },
    [],
  );

  const reset = useCallback(() => {
    setState({
      streaming: false,
      answer: "",
      sources: [],
      citations: [],
      degradedReasons: [],
      error: null,
      requestId: null,
    });
  }, []);

  return { state, start, reset };
}
