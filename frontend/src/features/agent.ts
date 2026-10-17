import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet } from "../api/client";
import { parseAgentEvent, type AgentEvent, type AgentRun, type AgentSkill } from "../types/agent";
export const terminal = (status: string) => ["succeeded", "failed", "cancelled"].includes(status);
export function useAgentSkills() {
  return useQuery({ queryKey: ["agent-skills"], queryFn: () => apiGet<AgentSkill[]>("/api/v1/agent/skills"), retry: false });
}
export function useAgentRun(id: string | null) {
  return useQuery({ queryKey: ["agent-run", id], queryFn: () => apiGet<AgentRun>(`/api/v1/agent/runs/${id}`), enabled: Boolean(id), retry: 1,
    refetchInterval: query => query.state.data && terminal(query.state.data.status) ? false : 2000 });
}
export function useAgentEvents(runId: string | null) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [answer, setAnswer] = useState("");
  const [connection, setConnection] = useState("idle");
  const [streamRunId, setStreamRunId] = useState<string | null>(null);
  const cursor = useRef(0);
  const qc = useQueryClient();
  useEffect(() => {
    cursor.current = 0;
    if (!runId) return;
    const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://127.0.0.1:8000";
    const stream = new EventSource(`${base}/api/v1/agent/runs/${runId}/events`);
    let firstOpen = true;
    stream.onopen = () => { if (firstOpen) { setEvents([]); setAnswer(""); setStreamRunId(runId); firstOpen = false; } setConnection("connected"); };
    stream.onerror = () => { setConnection("reconnecting"); void qc.invalidateQueries({ queryKey: ["agent-run", runId] }); };
    const receive = (event: MessageEvent<string>) => {
      let item: AgentEvent | null;
      try { item = parseAgentEvent(event.data); } catch { return; }
      if (!item || item.sequence <= cursor.current) return;
      cursor.current = item.sequence;
      setEvents(previous => [...previous, item].slice(-100));
      if (item.type === "answer.reset" || item.type === "answer.started") setAnswer("");
      if (item.type === "answer.delta" && typeof item.payload.text === "string") { const text = item.payload.text; setAnswer(previous => previous + text); }
      if (item.type === "answer.completed" && typeof item.payload.answer === "string") setAnswer(item.payload.answer);
      if (["run.completed", "run.failed", "run.cancelled"].includes(item.type)) { stream.close(); setConnection("complete"); void qc.invalidateQueries({ queryKey: ["agent-run", runId] }); }
    };
    const names = ["run.created", "run.started", "run.resumed", "skill.loaded", "action.selected", "action.rejected", "model.started", "tool.started", "tool.completed", "context.compacted", "budget.stopped", "answer.started", "answer.reset", "answer.delta", "answer.completed", "run.interrupted", "run.failed", "run.cancelled", "run.completed"];
    names.forEach(name => stream.addEventListener(name, receive));
    return () => stream.close();
  }, [runId, qc]);
  return streamRunId === runId ? { events, answer, connection } : { events: [], answer: "", connection: runId ? "connecting" : "idle" };
}
