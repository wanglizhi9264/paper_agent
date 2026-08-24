import { useState, useRef, useEffect, useCallback } from "react";
import { useSessions, useCreateSession, useDeleteSession, useSessionMessages, useChatStream } from "../features/chat";

interface SimpleMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  citations: { index: number; chunk_id: string }[] | null;
  created_at: string;
}

export function ChatPage() {
  const sessions = useSessions();
  const createSession = useCreateSession();
  const deleteSession = useDeleteSession();
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const messages = useSessionMessages(activeSession);
  const { state: sseState, start: startStream, reset: resetSSE } = useChatStream();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [sseState.answer, messages.data]);

  const handleCreate = useCallback(() => {
    createSession.mutate(
      { title: "New Chat", scope: { type: "all" } },
      {
        onSuccess: (s) => {
          setActiveSession(s.id);
          resetSSE();
        },
      },
    );
  }, [createSession, resetSSE]);

  const handleSend = useCallback(() => {
    if (!activeSession || !query.trim()) return;
    const q = query;
    setQuery("");
    startStream(activeSession, q);
  }, [activeSession, query, startStream]);

  const allMessages: SimpleMessage[] = [
    ...((messages.data?.items ?? []).map((m) => ({ ...m, role: m.role as SimpleMessage["role"] }))),
    ...(sseState.streaming || sseState.answer ? [{
      id: "streaming",
      role: "assistant" as const,
      content: sseState.answer,
      citations: null,
      created_at: new Date().toISOString(),
    }] : []),
  ];

  return (
    <section className="page page-chat">
      <div className="chat-sidebar">
        <button type="button" className="btn" onClick={handleCreate} disabled={createSession.isPending}>
          New Chat
        </button>
        {sessions.data?.items.map((s) => (
          <div
            key={s.id}
            className={`session-item ${activeSession === s.id ? "active" : ""}`}
            onClick={() => { setActiveSession(s.id); resetSSE(); }}
          >
            <span className="session-title">{s.title}</span>
            <button
              type="button"
              className="btn-sm btn-danger"
              onClick={(e) => { e.stopPropagation(); deleteSession.mutate(s.id); if (activeSession === s.id) setActiveSession(null); }}
            >
              ×
            </button>
          </div>
        ))}
        {sessions.data?.items.length === 0 && <p className="muted">No sessions.</p>}
      </div>

      <div className="chat-main">
        {!activeSession ? (
          <div className="chat-empty">
            <p className="muted">Create a new chat to start asking questions about your papers.</p>
          </div>
        ) : (
          <>
            <div className="chat-messages" ref={scrollRef}>
              {allMessages.length === 0 && <p className="muted">Ask a question to begin.</p>}
              {allMessages.map((msg, i) => (
                <div key={msg.id ?? i} className={`message message-${msg.role}`}>
                  <div className="message-content">
                    {msg.content || (sseState.streaming ? "…" : "")}
                    {msg.citations && msg.citations.length > 0 && (
                      <span className="citations">
                        {msg.citations.map((c) => (
                          <span key={c.index} className="citation-badge" title={c.chunk_id}>
                            [{c.index}]
                          </span>
                        ))}
                      </span>
                    )}
                  </div>
                </div>
              ))}
              {sseState.error && (
                <div className="message message-error">
                  <div className="message-content">Error: {sseState.error}</div>
                </div>
              )}
            </div>

            {sseState.sources.length > 0 && (
              <div className="source-drawer">
                <h3>Sources</h3>
                {sseState.sources.map((src) => (
                  <div key={src.index} className="source-item">
                    <div className="source-header">
                      <span className="source-index">[{src.index}]</span>
                      <span className="source-title">{src.document_title}</span>
                      <span className="muted">{src.page}</span>
                    </div>
                    <div className="source-section">{src.section_path.join(" > ")}</div>
                    <div className="source-content">{src.content}</div>
                    {src.truncated && <span className="muted">[truncated]</span>}
                  </div>
                ))}
              </div>
            )}

            <div className="chat-input-bar">
              <input
                type="text"
                placeholder="Ask about your papers…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
                className="text-input chat-input"
                disabled={sseState.streaming}
              />
              <button
                type="button"
                className="btn"
                onClick={handleSend}
                disabled={sseState.streaming || !query.trim()}
              >
                Send
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
