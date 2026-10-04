import { useEffect, useMemo, useState } from "react";
import { createReviewExport, parseReviewDataset, parseReviewReport, parsedPageFromIr } from "../features/goldReview";
import type { ReviewDataset, ReviewDecision, ReviewNote, ReviewReport } from "../types/goldReview";

const statusText: Record<string, string> = {
  parser_issue: "解析缺失",
  manual_review: "需要人工判断",
  unresolved: "未定位",
  resolved: "已定位",
  multi_chunk: "跨 Chunk",
};

const reasonText: Record<string, string> = {
  SOURCE_PRESENT_PARSER_OUTPUT_MISSING: "原 PDF 中有证据，但当前 parser 输出未保留。请先核对原文和解析链路。",
  FUZZY_CANDIDATES_REQUIRE_REVIEW: "仅找到近似候选。相似度不能证明候选就是 gold evidence。",
  MULTIPLE_SOURCE_MATCHES: "存在多个合理的原文匹配，不能自动任选一个。",
};

async function readJson(file: File): Promise<unknown> {
  return JSON.parse(await file.text()) as unknown;
}

export function GoldReviewPage() {
  const [dataset, setDataset] = useState<ReviewDataset | null>(null);
  const [report, setReport] = useState<ReviewReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [pdfs, setPdfs] = useState<Record<string, string>>({});
  const [autoPdfKeys, setAutoPdfKeys] = useState<string[]>([]);
  const [irByDocument, setIrByDocument] = useState<Record<string, unknown>>({});
  const [autoIrPages, setAutoIrPages] = useState<Record<string, { text: string; version_id: string }>>({});
  const [notes, setNotes] = useState<Record<string, ReviewNote>>({});
  const [autoLoading, setAutoLoading] = useState(true);

  useEffect(() => () => Object.values(pdfs).forEach((url) => URL.revokeObjectURL(url)), [pdfs]);

  useEffect(() => {
    let active = true;
    async function loadLocal() {
      const [datasetResult, reportResult, sourceResult] = await Promise.allSettled([
        fetch("/__local_gold_review/dataset", { cache: "no-store" }).then(async (response) => {
          if (!response.ok) throw new Error("Local dataset unavailable");
          return parseReviewDataset(await response.json() as unknown);
        }),
        fetch("/__local_gold_review/report", { cache: "no-store" }).then(async (response) => {
          if (!response.ok) throw new Error("Local report unavailable");
          return parseReviewReport(await response.json() as unknown);
        }),
        fetch("/__local_gold_review/sources", { cache: "no-store" }).then(async (response) => {
          if (!response.ok) throw new Error("Local PDF inventory unavailable");
          return await response.json() as { pdf_document_keys: string[] };
        }),
      ]);
      if (!active) return;
      if (datasetResult.status === "fulfilled") {
        setDataset(datasetResult.value);
        setSelectedId(datasetResult.value.dataset[0]?.id ?? null);
      }
      if (reportResult.status === "fulfilled") setReport(reportResult.value);
      if (sourceResult.status === "fulfilled") setAutoPdfKeys(sourceResult.value.pdf_document_keys);
      setAutoLoading(false);
    }
    void loadLocal();
    return () => { active = false; };
  }, []);

  const questions = useMemo(() => (dataset?.dataset ?? []).filter((item) => {
    const matchesStatus = filter === "all" || item.evidence_resolution_status === filter;
    const term = query.trim().toLocaleLowerCase();
    return matchesStatus && (!term || `${item.id} ${item.question} ${item.reference_answer}`.toLocaleLowerCase().includes(term));
  }), [dataset, filter, query]);
  const selected = questions.find((item) => item.id === selectedId) ?? questions[0];
  const selectedNote = selected ? notes[selected.id] : undefined;

  useEffect(() => {
    if (!selected) return;
    let active = true;
    for (const evidence of selected.evidence) {
      const key = `${evidence.document_key}:${evidence.page}`;
      if (autoIrPages[key]) continue;
      void fetch(`/__local_gold_review/ir-page/${encodeURIComponent(evidence.document_key)}/${evidence.page}`, { cache: "no-store" })
        .then(async (response) => {
          if (!response.ok) return null;
          return await response.json() as { text: string; version_id: string };
        }).then((value) => {
          if (active && value) setAutoIrPages((current) => ({ ...current, [key]: value }));
        }).catch(() => undefined);
    }
    return () => { active = false; };
  }, [selected, autoIrPages]);

  async function loadDataset(file: File | undefined) {
    if (!file) return;
    try {
      const parsed = parseReviewDataset(await readJson(file));
      setDataset(parsed);
      setSelectedId(parsed.dataset[0]?.id ?? null);
      setNotes({});
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取题目文件。");
    }
  }

  async function loadReport(file: File | undefined) {
    if (!file) return;
    try {
      setReport(parseReviewReport(await readJson(file)));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取修复报告。");
    }
  }

  function loadPdfs(files: FileList | null) {
    if (!files) return;
    const next: Record<string, string> = {};
    for (const file of files) {
      if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) continue;
      next[file.name.toLowerCase()] = URL.createObjectURL(file);
    }
    setPdfs(next);
  }

  async function loadIr(documentKey: string, file: File | undefined) {
    if (!file) return;
    try {
      const value = await readJson(file);
      parsedPageFromIr(value, 1);
      setIrByDocument((current) => ({ ...current, [documentKey]: value }));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取 Document IR。");
    }
  }

  function updateNote(decision: ReviewDecision, comment: string) {
    if (!selected) return;
    setNotes((current) => ({ ...current, [selected.id]: { question_id: selected.id, decision, comment } }));
  }

  function exportNotes() {
    const payload = createReviewExport(report, Object.values(notes).filter((note) => note.decision !== "not_reviewed" || note.comment.trim()));
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "gold-evidence-review-notes.json";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <section className="page page-gold-review">
    <header className="review-header">
      <div><h1>Gold Evidence 审视</h1><p className="muted">逐题核对原始论文、人工证据与解析候选。这里的笔记不会更改 gold label。</p></div>
      <button className="btn" type="button" disabled={!dataset} onClick={exportNotes}>导出审视笔记</button>
    </header>
    <details className="review-optional"><summary>替换数据或补充材料（可选）</summary><div className="review-imports">
      <label>异常题文件 <input aria-label="异常题文件" type="file" accept=".json,application/json" onChange={(event) => void loadDataset(event.target.files?.[0])} /></label>
      <label>修复报告 <input aria-label="修复报告" type="file" accept=".json,application/json" onChange={(event) => void loadReport(event.target.files?.[0])} /></label>
      <label>原始 PDF（可多选） <input aria-label="原始 PDF" type="file" accept=".pdf,application/pdf" multiple onChange={(event) => loadPdfs(event.target.files)} /></label>
    </div></details>
    <p className="review-help">异常题、修复报告及 SHA-256 匹配的原始 PDF 从本机自动加载；无需选文件。私有文件不会进入 production bundle。</p>
    {error && <div role="alert" className="error-box">{error}</div>}
    {report && <div className="review-summary" aria-label="审计统计">
      <span>总题数 <strong>{report.benchmark_total}</strong></span><span>可回答 <strong>{report.answerable_total}</strong></span>
      <span>已定位 <strong>{report.resolved}</strong></span><span>Parser 问题 <strong>{report.parser_issue}</strong></span>
      <span>人工审视 <strong>{report.manual_review}</strong></span><span>跨 Chunk <strong>{report.multi_chunk}</strong></span>
      <span>未定位 <strong>{report.unresolved}</strong></span>
      <span className="review-freeze">{report.freeze_permitted ? "允许冻结" : "未允许冻结"}</span>
    </div>}
    {!dataset ? <div className="review-empty"><h2>{autoLoading ? "正在加载本机异常题…" : "本机异常题不可用"}</h2><p>{autoLoading ? "请稍候。" : "确认 eval/private_benchmark/repair 下的报告存在，或手动选择异常题 JSON。"}</p></div> :
      <div className="review-layout">
        <aside className="review-list" aria-label="异常题列表">
          <div className="review-filters"><input className="text-input" aria-label="搜索题目" placeholder="搜索编号或问题" value={query} onChange={(event) => setQuery(event.target.value)} />
            <select aria-label="按状态筛选" value={filter} onChange={(event) => setFilter(event.target.value)}>
              <option value="all">全部状态</option><option value="parser_issue">解析缺失</option><option value="manual_review">人工判断</option><option value="unresolved">未定位</option>
            </select></div>
          <p className="muted">显示 {questions.length} 题 · 已写笔记 {Object.keys(notes).length} 题</p>
          {questions.map((item) => <button type="button" className={`review-list-item${selected?.id === item.id ? " active" : ""}`} key={item.id} onClick={() => setSelectedId(item.id)}>
            <span className="review-list-top"><strong>{item.id}</strong><span className={`review-status ${item.evidence_resolution_status}`}>{statusText[item.evidence_resolution_status] ?? item.evidence_resolution_status}</span></span>
            <span>{item.question}</span>{notes[item.id] && <small>已有审视笔记</small>}
          </button>)}
          {questions.length === 0 && <p className="muted">没有符合筛选条件的题目。</p>}
        </aside>
        <article className="review-detail">
          {selected ? <>
            <div className="review-detail-head"><span className="muted">{selected.id}</span><span className={`review-status ${selected.evidence_resolution_status}`}>{statusText[selected.evidence_resolution_status] ?? selected.evidence_resolution_status}</span></div>
            <h2>{selected.question}</h2>
            <section className="review-panel"><h3>参考答案（只读）</h3><p>{selected.reference_answer}</p></section>
            {selected.evidence.map((evidence, index) => {
              const document = dataset.documents.find((item) => item.document_key === evidence.document_key);
              const pdf = document ? pdfs[document.filename.toLowerCase()] ??
                (autoPdfKeys.includes(evidence.document_key) ? `/__local_gold_review/pdf/${encodeURIComponent(evidence.document_key)}` : undefined) : undefined;
              const parsedPage = autoIrPages[`${evidence.document_key}:${evidence.page}`];
              return <section className="review-evidence" key={`${selected.id}-${index}`}>
                <div className="review-evidence-head"><h3>证据 {index + 1}</h3><span className={`review-status ${evidence.resolution.status}`}>{statusText[evidence.resolution.status] ?? evidence.resolution.status}</span></div>
                <p className="muted">{document?.title ?? evidence.document_key} · 第 {evidence.page} 页 · {evidence.section_path.join(" / ") || "章节未知"}</p>
                <div className="review-compare"><div className="review-panel"><h4>人工 Gold Evidence</h4><blockquote>{evidence.quote}</blockquote></div>
                  <div className="review-panel"><h4>匹配诊断</h4><p>{reasonText[evidence.resolution.reason_code] ?? evidence.resolution.reason_code}</p><p className="muted">方法：{evidence.resolution.match_method} · {evidence.resolution.resolved_chunk_ids.length ? `已解析 ${evidence.resolution.resolved_chunk_ids.length} 个 Chunk` : "尚未确认 Chunk"}</p></div></div>
                <h4>候选 Chunk（仅供核对，未自动设为 Gold）</h4>
                {evidence.resolution.candidates.length ? <div className="review-candidates">{evidence.resolution.candidates.map((candidate, candidateIndex) => <div className="review-candidate" key={candidateIndex}>
                  <div><strong>候选 {candidateIndex + 1}</strong> · token coverage {(candidate.token_coverage * 100).toFixed(1)}% · section {candidate.section_matches ? "匹配" : "不匹配"}</div>
                  <code>{candidate.chunk_ids.join(" + ")}</code>
                  {candidate.raw_content ? <pre>{candidate.raw_content}</pre> : <p className="muted">此离线报告没有保存候选 Chunk 正文；不能仅凭 ID 或相似度确认。</p>}
                </div>)}</div> : <p className="muted">无候选 Chunk。</p>}
                <div className="review-panel"><h4>原始 PDF · 第 {evidence.page} 页</h4>
                  {pdf ? <iframe title={`${document?.title ?? evidence.document_key} 第 ${evidence.page} 页`} src={`${pdf}#page=${evidence.page}`} /> : <p className="muted">尚未加载这篇论文的 PDF。请选择原始 PDF，并人工核对页面、公式和表格。</p>}</div>
                <div className="review-panel"><h4>当前 Parser / Document IR · 第 {evidence.page} 页</h4>
                  {parsedPage ? <>
                    <p className="muted">自动匹配 active snapshot 的 DocumentVersion：<code>{parsedPage.version_id}</code></p>
                    <pre className="review-ir-text">{parsedPage.text || "该页在 active IR 中没有文本元素。"}</pre>
                  </> : <>
                    <label className="muted">自动匹配失败时，可选择这篇论文 active version 的 <code>document_ir.json</code>： <input aria-label={`导入 ${evidence.document_key} Document IR`} type="file" accept=".json,application/json" onChange={(event) => void loadIr(evidence.document_key, event.target.files?.[0])} /></label>
                    {irByDocument[evidence.document_key] ? <pre className="review-ir-text">{parsedPageFromIr(irByDocument[evidence.document_key], evidence.page) || "该页在所选 IR 中没有文本元素；请确认所选 IR 属于当前 active version。"}</pre> : <p className="muted">当前无法自动确认 parser 输出；不要仅凭 PDF 和候选 ID 判断 parser_issue。</p>}
                  </>}
                </div>
              </section>;
            })}
            <section className="review-panel review-notes"><h3>你的审视结论（独立笔记）</h3>
              <label>判断 <select aria-label="审视判断" value={selectedNote?.decision ?? "not_reviewed"} onChange={(event) => updateNote(event.target.value as ReviewDecision, selectedNote?.comment ?? "")}>
                <option value="not_reviewed">尚未审视</option><option value="evidence_confirmed">原文证据确认；待正式解析</option><option value="parser_fix_needed">需要修复 Parser</option><option value="label_correction_needed">原 Gold Evidence 疑似错误/歧义</option><option value="uncertain">暂不能判断</option>
              </select></label>
              <label>依据与待办 <textarea aria-label="审视备注" placeholder="记录 PDF 页内位置、正确原文、候选之间的差别；不要在此直接改 Gold。" value={selectedNote?.comment ?? ""} onChange={(event) => updateNote(selectedNote?.decision ?? "not_reviewed", event.target.value)} /></label>
              <p className="muted">导出的 JSON 只是审视笔记，不会进入评测，也不会解除 freeze gate。</p>
            </section>
          </> : <p className="muted">请选择一道题。</p>}
        </article>
      </div>}
  </section>;
}
