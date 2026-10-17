import type { ChatSource } from './chat';

export interface TraceCandidate {
  chunk_id: string; document_id: string; document_title: string; section_path: string[];
  page_start: number | null; page_end: number | null; raw_content: string; retrieval_content: string;
  raw_truncated: boolean; retrieval_truncated: boolean;
}
export interface TraceStep {
  sequence: number; call_id: string; tool: string; status: string;
  arguments: Record<string, unknown> | null; started_at: string | null; completed_at: string | null;
  duration_ms: number | null; error_code: string | null; output: unknown;
  retrieval: Record<string, unknown> | null; candidates: TraceCandidate[];
  candidates_truncated: boolean; unavailable_candidates: number;
}
export interface AgentTrace {
  schema_version: 1; run_id: string; query: string; status: string;
  availability: 'recorded' | 'not_recorded' | 'expired'; created_at: string; finished_at: string | null;
  snapshot_id: string; scope: Record<string, unknown>; model_id: string; context_limit: number | null;
  loaded_skills: Record<string, string>; usage: Record<string, number>; stop_reason: string;
  error_code: string | null; answer: string | null; steps: TraceStep[];
  events: { sequence: number; type: string; timestamp: string; payload: Record<string, unknown> }[];
  sources: ChatSource[]; observed_evidence_count: number; context: Record<string, unknown>;
}
export interface Ranked { id: number; score: number; source: string; rank: number }
export function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
export function rows(value: unknown): Ranked[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item: unknown, index) => Array.isArray(item) && typeof item[0] === 'number' && typeof item[1] === 'number'
    ? [{ id: item[0], score: item[1], source: typeof item[2] === 'string' ? item[2] : '', rank: index + 1 }] : []);
}
export function contributions(debug: Record<string, unknown>, id: number) {
  const k = typeof debug.rrf_k === 'number' ? debug.rrf_k : 60;
  return Object.entries(record(debug.fusion_lists)).map(([name, value]) => {
    const hit = rows(value).find(row => row.id === id);
    return { name, rank: hit?.rank ?? null, value: hit ? 1 / (k + hit.rank) : 0 };
  });
}
