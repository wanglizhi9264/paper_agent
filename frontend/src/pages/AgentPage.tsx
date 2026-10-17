import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiPost } from "../api/client";
import { useAgentEvents, useAgentRun, useAgentSkills, terminal } from "../features/agent";
import { useDocuments } from "../features/documents";
import type { AgentRun } from "../types/agent";
import type { SessionItem } from "../types/chat";

export function AgentPage() {
  const [runId, setRunId] = useState<string | null>(() => localStorage.getItem("paper-agent-run"));
  const [sessionId, setSessionId] = useState<string | null>(() => localStorage.getItem("paper-agent-session"));
  const [query, setQuery] = useState("");
  const [skill, setSkill] = useState("paper-reading");
  const [selected, setSelected] = useState<string[]>([]);
  const [sourceIndex, setSourceIndex] = useState<number | null>(null);
  const catalog = useAgentSkills();
  const documents = useDocuments();
  const run = useAgentRun(runId);
  const stream = useAgentEvents(runId);
  const active = run.data && !terminal(run.data.status);
  const start = useMutation({ mutationFn: async () => {
    let session = sessionId;
    if (!session) {
      const created = await apiPost<SessionItem>("/api/v1/sessions", { title: "论文 Agent", scope: selected.length ? { type: "documents", document_ids: selected } : { type: "all" } });
      session = created.id; setSessionId(session); localStorage.setItem("paper-agent-session", session);
    }
    return apiPost<AgentRun>("/api/v1/agent/runs", { session_id: session, query, mode: "agent", skill_names: skill ? [skill] : [] }, { "Idempotency-Key": crypto.randomUUID() });
  }, onSuccess: value => { setRunId(value.run_id); localStorage.setItem("paper-agent-run", value.run_id); setQuery(""); } });
  const control = useMutation({ mutationFn: (action: "cancel" | "resume") => apiPost<AgentRun>(`/api/v1/agent/runs/${runId}/${action}`, {}, { "Idempotency-Key": crypto.randomUUID() }), onSuccess: () => { void run.refetch(); } });
  const error = catalog.error ?? documents.error ?? start.error ?? control.error ?? run.error;
  const answer = run.data?.answer ?? stream.answer;
  const sources = run.data?.sources ?? [];
  return <section className="page agent-page">
    <h1>论文 Agent</h1><p className="muted">选择论文与阅读方式，查看工具进度和原文证据。会话范围创建后固定。</p>
    {catalog.isLoading && <p role="status">正在加载技能…</p>}
    {error && <p role="alert">{error.message}</p>}
    {catalog.data?.length === 0 && <p>没有可用技能。</p>}
    <div className="agent-controls">
      <label>阅读方式 <select value={skill} onChange={e => setSkill(e.target.value)} disabled={Boolean(active)}><option value="">自动选择</option>{catalog.data?.map(s => <option key={s.name} value={s.name}>{s.name} — {s.description}</option>)}</select></label>
      {!sessionId && <fieldset><legend>论文范围（不选则使用当前全部论文）</legend>{documents.data?.items.filter(d => d.status === "ready").map(d => <label key={d.id}><input type="checkbox" checked={selected.includes(d.id)} onChange={e => setSelected(previous => e.target.checked ? [...previous, d.id] : previous.filter(id => id !== d.id))} />{d.title ?? d.filename}</label>)}{documents.data?.items.length === 0 && <p>请先在 Documents 上传论文。</p>}</fieldset>}
      <label>问题<textarea value={query} onChange={e => setQuery(e.target.value)} disabled={Boolean(active)} placeholder="比较两篇论文的方法与实验条件，并指出证据缺口" /></label>
      <div className="agent-actions"><button className="btn" onClick={() => start.mutate()} disabled={Boolean(active) || start.isPending || !query.trim() || !catalog.data}>开始分析</button>
      <button className="btn" disabled={Boolean(active)} onClick={() => { setSessionId(null); setRunId(null); localStorage.removeItem("paper-agent-session"); localStorage.removeItem("paper-agent-run"); }}>新会话</button>
      {active && <button className="btn" disabled={control.isPending} onClick={() => { if (window.confirm("取消正在执行的分析？")) control.mutate("cancel"); }}>取消分析</button>}
      {run.data?.status === "interrupted" && <button className="btn" disabled={control.isPending} onClick={() => control.mutate("resume")}>继续执行</button>}</div>
    </div>
    {!runId && <p className="muted">尚未开始分析。</p>}
    {run.isLoading && runId && <p role="status">恢复执行记录…</p>}
    {run.data && <div className="agent-result"><p role="status">状态：{run.data.status} · 连接：{stream.connection} · 工具 {run.data.usage.tool_calls}/10 · 模型 {run.data.usage.llm_calls}/8 · tokens {run.data.usage.tokens}/32000</p>
      <p>技能：{Object.keys(run.data.selected_skills).join("、") || "按任务选择"} · 停止原因：{run.data.stop_reason}{run.data.error_code && ` · ${run.data.error_code}`}</p>
      <details open><summary>执行过程</summary><ol>{(run.data.steps.length ? run.data.steps.map(s => `${s.tool}: ${s.status}`) : stream.events.filter(e => e.type !== "answer.delta").map(e => e.type)).map((text, i) => <li key={i}>{text}</li>)}</ol></details>
      {answer && <div className="agent-answer">{answer}</div>}
      {run.data.claims?.length > 0 && <details><summary>结论与证据对应</summary><table><thead><tr><th>结论段落</th><th>原文来源</th></tr></thead><tbody>{run.data.claims.map((claim,index) => <tr key={index}><td>{claim.text}</td><td>{claim.status === "gap" ? "缺少引用，需核验" : claim.source_ids.map(id => { const source = sources.find(s => s.chunk_id === id); return source ? <button className="btn" key={id} onClick={() => setSourceIndex(source.index)}>[{source.index}]</button> : null; })}</td></tr>)}</tbody></table></details>}
      {sources.length > 0 && <><h2>来源</h2><div>{sources.map(s => <button key={s.index} className="btn" onClick={() => setSourceIndex(s.index)}>[{s.index}] {s.document_title}</button>)}</div>
      {sources.filter(s => sourceIndex === null || s.index === sourceIndex).map(s => <article key={s.chunk_id} className="source-item"><h3>[{s.index}] {s.document_title}</h3><p>{s.section_path.join(" › ")} · 页 {s.page}</p><p className="agent-answer">{s.content}</p>{s.truncated && <small>原文已按预算截断</small>}</article>)}</>}
    </div>}
  </section>;
}
