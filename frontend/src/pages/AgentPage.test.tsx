import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AgentRun } from "../types/agent";
import { AgentPage } from "./AgentPage";

const state = vi.hoisted(() => ({ loading: false, error: null as Error | null, run: undefined as AgentRun | undefined }));
vi.mock("../features/agent", () => ({
  terminal: (status: string) => ["succeeded", "failed", "cancelled"].includes(status),
  useAgentSkills: () => ({ data: [{ name: "paper-reading", description: "精读", digest: "digest" }], isLoading: state.loading, error: state.error }),
  useAgentRun: () => ({ data: state.run, isLoading: false, error: null, refetch: vi.fn() }),
  useAgentEvents: () => ({ events: [], answer: "", connection: "complete" }),
}));
vi.mock("../features/documents", () => ({ useDocuments: () => ({ data: { items: [] }, error: null }) }));

function page() {
  render(<QueryClientProvider client={new QueryClient()}><AgentPage /></QueryClientProvider>);
}

describe("Agent page", () => {
  beforeEach(() => { state.loading = false; state.error = null; state.run = undefined; localStorage.clear(); });
  it("shows loading, empty and configuration failure states", () => {
    state.loading = true;
    state.error = new Error("AGENT_DISABLED");
    page();
    expect(screen.getByText("正在加载技能…")).toBeInTheDocument();
    expect(screen.getByText("尚未开始分析。")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("AGENT_DISABLED");
    expect(screen.getByRole("button", { name: "开始分析" })).toBeDisabled();
  });
  it("shows persisted answers and sources bound to actual chunk IDs", () => {
    localStorage.setItem("paper-agent-run", "run-id");
    state.run = { run_id: "run-id", session_id: "session-id", snapshot_id: "snapshot-id", model_id: "model", status: "succeeded", events_url: "events", usage: { llm_calls: 2, tool_calls: 1, search_calls: 1, tokens: 100 }, selected_skills: { "paper-reading": "digest" }, stop_reason: "completed", error_code: null, final_message_id: "message-id", answer: "Supported result [1].", claims: [{ text: "Supported result [1].", source_ids: ["chunk-id"], status: "cited" }], steps: [], created_at: "2026-10-17T00:00:00Z", sources: [{ index: 1, chunk_id: "chunk-id", document_id: "document-id", document_title: "Fixture paper", section_path: ["Method"], page: "1", page_start: 1, page_end: 1, element_id: null, element_kind: null, cell_ids: [], bboxes: [], content: "Original evidence.", truncated: false }] };
    page();
    expect(screen.getByText("Supported result [1].", { selector: ".agent-answer" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "[1] Fixture paper" }));
    expect(screen.getByText("Original evidence.")).toBeInTheDocument();
    expect(screen.getByText("结论与证据对应")).toBeInTheDocument();
  });
});
