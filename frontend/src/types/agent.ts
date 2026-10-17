import type { ChatSource } from "./chat";
export type AgentStatus = "queued" | "running" | "interrupted" | "succeeded" | "failed" | "cancelled";
export interface AgentSkill { name: string; description: string; digest: string }
export interface AgentRun {
  run_id: string; session_id: string; snapshot_id: string; model_id: string; status: AgentStatus; events_url: string;
  usage: { llm_calls: number; tool_calls: number; search_calls: number; tokens: number };
  selected_skills: Record<string, string>; stop_reason: string; error_code: string | null;
  final_message_id: string | null; answer: string | null; sources: ChatSource[];
  claims: { text: string; source_ids: string[]; status: "cited" | "gap" }[];
  steps: { sequence: number; tool: string; status: string; call_id: string }[];
  created_at: string;
}
export interface AgentEvent { sequence: number; type: string; payload: Record<string, unknown> }
export function parseAgentEvent(raw: string): AgentEvent | null {
  const value: unknown = JSON.parse(raw);
  if (typeof value !== "object" || value === null || !("sequence" in value) || !("type" in value) || !("payload" in value)) return null;
  if (typeof value.sequence !== "number" || !Number.isSafeInteger(value.sequence) || typeof value.type !== "string" || typeof value.payload !== "object" || value.payload === null || Array.isArray(value.payload)) return null;
  return { sequence: value.sequence, type: value.type, payload: value.payload as Record<string, unknown> };
}
