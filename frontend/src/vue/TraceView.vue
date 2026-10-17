<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue';
import { useMutation, useQuery } from '@tanstack/vue-query';
import { apiGet, apiPost } from '../api/client';
import type { AgentRun } from '../types/agent';
import { contributions, record, rows, type AgentTrace, type Ranked } from '../types/trace';

const props = defineProps<{ runId: string }>();
const trace = useQuery({ queryKey: computed(() => ['agent-trace', props.runId]), queryFn: () => apiGet<AgentTrace>(`/api/v1/agent/runs/${props.runId}/trace`), retry: false, refetchInterval: q => q.state.data && ['succeeded', 'failed', 'cancelled'].includes(q.state.data.status) ? false : 2000 });
const selectedStep = ref<number | null>(null);
const stage = ref('query');
const view = ref('original');
const focus = ref<number | null>(null);
const filter = ref('');
const now = ref(Date.now());
const timer = window.setInterval(() => { now.value = Date.now(); }, 1000);
onScopeDispose(() => window.clearInterval(timer));
watch(() => props.runId, () => { selectedStep.value = null; stage.value = 'query'; focus.value = null; });
const data = computed(() => trace.data.value);
const active = computed(() => data.value?.steps.find(s => s.sequence === selectedStep.value) ?? data.value?.steps.find(s => s.tool === 'search_papers') ?? data.value?.steps[0]);
const debug = computed(() => record(active.value?.retrieval));
const manifest = computed(() => record(debug.value.embedding_manifest));
const index = computed(() => record(debug.value.index));
const reranker = computed(() => record(debug.value.reranker));
const queryPlan = computed(() => record(debug.value.retrieval_queries));
const bm25 = computed(() => record(record(debug.value.bm25_explanations)[view.value]));
const hasEnglish = computed(() => Boolean(queryPlan.value.english));
const tools: Record<string, string> = { list_papers: '定位论文', search_papers: '混合检索', read_chunks: '读取原文', read_table: '读取表格', load_skill: '加载 Skill', read_skill_resource: '读取 Skill 资源' };
const statuses: Record<string, string> = { queued: '等待 Worker', running: '执行中', interrupted: '已中断', succeeded: '完成', failed: '失败', cancelled: '已取消' };
const stages = [ { id: 'query', label: '01 问题与查询' }, { id: 'bm25', label: '02 BM25' }, { id: 'dense', label: '03 FAISS' }, { id: 'rrf', label: '04 RRF 融合' }, { id: 'rerank', label: '05 精排与选择' }, { id: 'context', label: '06 上下文与引用' }, { id: 'events', label: '07 事件时间线' } ];
const listKey = computed(() => stage.value === 'bm25' ? `bm25_${view.value}` : stage.value === 'dense' ? `dense_${view.value}` : stage.value === 'rrf' ? 'rrf' : 'rerank');
const ranked = computed(() => rows(debug.value[listKey.value]));
const candidateMap = computed(() => new Map(active.value?.candidates.map(c => [c.chunk_id, c]) ?? []));
const chunkIds = computed(() => record(debug.value.candidate_chunk_ids));
function candidate(id: number) { return candidateMap.value.get(String(chunkIds.value[String(id)] ?? '')); }
const visibleRows = computed(() => ranked.value.filter(r => {
  const c = candidate(r.id);
  return !filter.value || `${r.id} ${c?.document_title ?? ''} ${c?.raw_content ?? ''} ${c?.section_path.join(' ') ?? ''}`.toLowerCase().includes(filter.value.toLowerCase());
}));
const selectedRow = computed(() => ranked.value.find(r => r.id === focus.value) ?? visibleRows.value[0]);
const selectedCandidate = computed(() => selectedRow.value ? candidate(selectedRow.value.id) : undefined);
const bm25Candidate = computed(() => record(record(bm25.value.candidates)[String(selectedRow.value?.id)]));
const termRows = computed(() => Array.isArray(bm25Candidate.value.terms) ? bm25Candidate.value.terms.map(record) : []);
const rrfParts = computed(() => selectedRow.value ? contributions(debug.value, selectedRow.value.id) : []);
const rrfSum = computed(() => rrfParts.value.reduce((sum, part) => sum + part.value, 0));
const decisions = computed(() => Array.isArray(debug.value.selection_decisions) ? debug.value.selection_decisions.map(record) : []);
const timings = computed(() => Object.entries(record(debug.value.timings_ms)).filter(([, v]) => typeof v === 'number'));
const timingLabels: Record<string, string> = { scope: '准备总计（含快照、模型就绪、范围 DB）', snapshot: '快照读取', embedding_model_ready: 'Embedding 模型就绪（可能含冷加载）', scope_db: '范围 / Chunk 元数据查询', faiss_index_load: 'FAISS 文件加载（已含在 Dense）', bm25_index_load: 'BM25 文件加载（已含在 BM25）', embedding: '查询向量编码', dense: 'Dense 检索（含文件加载）', bm25: 'BM25 检索（含文件加载）', fusion: 'RRF 融合', rerank: 'Cross-encoder 精排', selection: '证据选择', total: '检索总计（不含 GPU 等待）' };
const busy = computed(() => data.value && !['succeeded', 'failed', 'cancelled', 'interrupted'].includes(data.value.status));
const elapsed = computed(() => data.value ? Math.max(0, ((data.value.finished_at ? Date.parse(data.value.finished_at) : now.value) - Date.parse(data.value.created_at)) / 1000) : 0);
const control = useMutation({ mutationFn: (action: 'cancel' | 'resume') => apiPost<AgentRun>(`/api/v1/agent/runs/${props.runId}/${action}`, {}, { 'Idempotency-Key': crypto.randomUUID() }), onSuccess: () => { void trace.refetch(); } });
function cancel() { if (window.confirm('取消本次运行？已记录的执行链路会保留。')) control.mutate('cancel'); }
function selectStep(sequence: number) { selectedStep.value = sequence; focus.value = null; filter.value = ''; view.value = 'original'; }
function number(value: unknown, digits = 5) { return typeof value === 'number' ? value.toFixed(digits) : '—'; }
function json(value: unknown) { return JSON.stringify(value, null, 2); }
function rankAt(key: string, row: Ranked) { return rows(debug.value[key]).find(r => r.id === row.id)?.rank ?? '—'; }
function selected(id: number) { return rows(debug.value.selected).some(r => r.id === id); }
function offset(timestamp: string) { return data.value ? ((Date.parse(timestamp) - Date.parse(data.value.created_at)) / 1000).toFixed(2) : '0'; }
function download() {
  if (!data.value) return;
  const url = URL.createObjectURL(new Blob([json(data.value)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = `agent-trace-${props.runId}.json`; a.click(); URL.revokeObjectURL(url);
}
</script>

<template>
  <section class="trace-workbench">
    <div v-if="trace.isLoading.value" role="status" class="trace-empty">正在读取执行链路…</div>
    <div v-else-if="trace.error.value" role="alert" class="trace-warning">无法读取链路：{{ trace.error.value.message }} <button @click="trace.refetch()">重试</button></div>
    <template v-else-if="data">
      <div class="trace-run-heading"><div><span class="trace-status" :class="{ 'trace-status-busy': busy }">{{ statuses[data.status] ?? data.status }}</span><strong>{{ data.query }}</strong><small>{{ data.model_id }} · {{ elapsed.toFixed(1) }}s · {{ data.usage.llm_calls }} 次模型调用 · {{ data.usage.tool_calls }} 次工具调用</small></div><div class="trace-controls"><button v-if="busy" :disabled="control.isPending.value" @click="cancel">取消运行</button><button v-if="data.status === 'interrupted'" :disabled="control.isPending.value" @click="control.mutate('resume')">继续执行</button><button @click="trace.refetch()">刷新</button><button @click="download">导出 JSON</button></div></div>
      <p v-if="control.error.value" role="alert" class="trace-warning">{{ control.error.value.message }}</p>
      <p v-if="data.error_code" class="trace-warning" role="alert">本次运行失败：{{ data.error_code }}。已完成的记录仍可查看。</p>
      <p v-if="data.availability === 'not_recorded'" class="trace-warning">这次运行没有启用详细 Trace，历史得分与搜索参数未记录。请重新运行一个问题以观察新链路；这里不会重跑并补造旧记录。</p>
      <p v-else-if="data.availability === 'expired'" class="trace-warning">详细链路已超过保留期限。最终答案和引用仍可查看。</p>
      <div class="trace-layout">
        <aside class="trace-calls"><div class="trace-overline">实际工具调用</div><button v-for="step in data.steps" :key="step.call_id" :class="{ active: active?.sequence === step.sequence }" @click="selectStep(step.sequence)"><span class="trace-call-number">{{ step.sequence.toString().padStart(2, '0') }}</span><span><strong>{{ tools[step.tool] ?? step.tool }}</strong><small>{{ step.tool }}</small><small>{{ step.status === 'running' ? '进行中' : statuses[step.status] ?? step.status }} · {{ step.duration_ms === null ? '耗时未记录' : `${(step.duration_ms / 1000).toFixed(2)}s` }}</small></span><span>{{ step.status === 'failed' ? '!' : step.status === 'running' ? '◌' : '✓' }}</span></button><p v-if="!data.steps.length">{{ busy ? '等待模型选择工具…' : '没有工具执行记录。' }}</p><div class="trace-pin"><small>固定索引快照</small><code>{{ data.snapshot_id }}</code><small>已加载 Skill</small><span>{{ Object.keys(data.loaded_skills).join('、') || '尚未加载' }}</span><small>结束原因</small><span>{{ busy ? '执行中' : data.stop_reason }}</span></div></aside>
        <div class="trace-main">
          <nav class="trace-tabs" aria-label="链路阶段"><button v-for="tab in stages" :key="tab.id" :class="{ active: stage === tab.id }" @click="stage = tab.id; focus = null; filter = ''">{{ tab.label }}</button></nav>
          <section v-if="stage === 'query'" class="trace-stage">
            <div class="trace-overline">OBSERVED INPUTS</div><h2>用户问题如何变成搜索词</h2><div class="trace-query-flow"><div><small>用户原始输入</small><p>{{ data.query }}</p></div><span>↓ Agent 选择工具参数</span><div><small>本次 search_papers.query</small><p>{{ active?.tool === 'search_papers' ? active.arguments?.query ?? '此记录没有保存参数' : '请选择左侧的一次混合检索' }}</p></div></div>
            <p v-if="active?.tool === 'search_papers' && active.arguments?.query" class="trace-note">{{ active.arguments.query === data.query ? '本次搜索词与原始问题完全一致。' : '本次搜索词与原始问题不同，这是 Agent 实际选择的查询。' }} Agent 入口没有固定的独立改写步骤，也不保证自动翻译成英文。</p>
            <div v-if="active?.retrieval" class="trace-facts"><div><small>Dense 实际查询</small><code>{{ queryPlan.original }}</code></div><div><small>BM25 实际查询</small><code>{{ record(record(debug.bm25_explanations).original).query }}</code></div><div><small>额外英文查询</small><code>{{ queryPlan.english ?? '未执行' }}</code></div><div><small>精排实际查询</small><code>{{ debug.rerank_query }}</code></div></div>
            <p class="trace-note">旧聊天入口另有历史感知 query rewrite；本页记录的是 Agent 入口。MDM / FID 只说明方法和指标，检索结果仍需核对数据集、实验设置和表格行列。</p>
            <details v-if="active"><summary>本次工具的完整参数和返回结果</summary><h3>输入参数</h3><pre>{{ json(active.arguments) }}</pre><h3>观察结果 · 论文文本属于不可信数据</h3><pre>{{ json(active.output) }}</pre><p v-if="active.error_code" class="trace-warning">{{ active.error_code }}</p></details>
            <details><summary>本次固定检索范围</summary><pre>{{ json(data.scope) }}</pre></details>
          </section>
          <section v-else-if="stage === 'context'" class="trace-stage">
            <div class="trace-overline">GROUNDING &amp; CONTEXT</div><h2>哪些证据真正进入答案上下文</h2><p class="trace-note">检索命中不等于最后被引用。Agent 会合并多次工具读取的证据，再按 token 预算打包；引用编号只对应实际打包的唯一 Chunk。</p>
            <div class="trace-metrics"><div><small>观察到的证据</small><strong>{{ data.observed_evidence_count }}</strong></div><div><small>最终打包来源</small><strong>{{ data.sources.length }}</strong></div><div><small>最终请求输入 tokens</small><strong>{{ data.context.input_tokens ?? '—' }}</strong></div><div><small>上下文上限</small><strong>{{ data.context_limit ?? '—' }}</strong></div></div>
            <p v-if="!Object.keys(data.context).length" class="trace-note">最终上下文尚未构建或该记录未保存 token 明细。</p><p v-else class="trace-note">预留输出 {{ data.context.output_reserved_tokens }} tokens + 安全余量 {{ data.context.safety_margin_tokens }}；收到历史 {{ data.context.history_messages_received }} 条；{{ data.context.summary_used ? '已使用会话摘要' : '未使用会话摘要' }}；{{ data.context.context_retry_used ? '发生过上下文压缩重试' : '未发生上下文压缩重试' }}。累计 tokens 是预算记账，包含预留量，不能当作账单。</p>
            <p v-if="!data.sources.length" class="trace-empty">{{ busy ? '等待最终来源打包和引用校验…' : '本次没有通过最终校验的打包来源。' }}</p>
            <details v-for="source in data.sources" :key="source.chunk_id" class="trace-source"><summary><b>[{{ source.index }}]</b> {{ source.document_title }} <small>第 {{ source.page }} 页</small></summary><p>{{ source.section_path.join(' › ') || '未识别章节' }} · {{ source.element_kind ?? '正文' }} · {{ source.cell_ids.length }} 个表格单元格</p><pre>{{ source.content }}</pre><p v-if="source.truncated">已按上下文预算截断。</p><code>{{ source.chunk_id }}</code></details>
            <details v-if="data.answer"><summary>最终回答</summary><pre>{{ data.answer }}</pre></details><details><summary>上下文打包明细</summary><pre>{{ json(data.context) }}</pre></details>
          </section>
          <section v-else-if="stage === 'events'" class="trace-stage">
            <div class="trace-overline">PERSISTED EVENTS</div><h2>一次运行的时间线</h2><p class="trace-note">时间从创建运行开始。model.started → model.completed 是模型调用；tool.started → tool.completed 包含 GPU 排队、检索、读取与保存。下方检索子阶段已包含在工具耗时内，不能重复相加。流式文本增量在此省略。</p>
            <ol class="trace-timeline"><li v-for="event in data.events.filter(e => e.type !== 'answer.delta')" :key="event.sequence"><time>+{{ offset(event.timestamp) }}s</time><details><summary>{{ event.type }} <small>#{{ event.sequence }}</small></summary><pre>{{ json(event.payload) }}</pre></details></li></ol><p v-if="!data.events.length">没有保留的事件记录。</p>
          </section>
          <section v-else class="trace-stage">
            <template v-if="!active?.retrieval"><h2>这一步还没有检索明细</h2><p>{{ active?.tool !== 'search_papers' ? '请选择左侧的一次混合检索。其他工具的输入输出可在“问题与查询”查看。' : active.status === 'running' ? '检索正在执行，完成后显示实际候选与得分。' : active.error_code ? `检索失败：${active.error_code}，没有完整阶段数据。` : '本次没有保存检索调试数据，或检索范围为空。' }}</p></template>
            <template v-else>
              <div class="trace-stage-heading"><div><div class="trace-overline">{{ stage === 'bm25' ? 'LEXICAL RETRIEVAL' : stage === 'dense' ? 'SEMANTIC RETRIEVAL' : stage === 'rrf' ? 'RECIPROCAL RANK FUSION' : 'CROSS-ENCODER & SELECTION' }}</div><h2>{{ stage === 'bm25' ? 'BM25：哪些词命中了哪些片段' : stage === 'dense' ? 'FAISS：用向量寻找语义相近片段' : stage === 'rrf' ? 'RRF：按名次融合两路候选' : '精排：重新比较问题和候选内容' }}</h2></div><label v-if="hasEnglish && ['dense', 'bm25'].includes(stage)">查询视图 <select v-model="view"><option value="original">原始检索词</option><option value="english">英文检索词</option></select></label></div>
              <template v-if="stage === 'bm25'"><p class="trace-note">查询经与建索引相同的分析器分词，在倒排表找候选；先限制 scope，再取 Top-K。N、df、平均长度来自整个索引的 chunk 统计，不是论文篇数。</p><div class="trace-token-list"><span v-for="(token, i) in (Array.isArray(bm25.tokens) ? bm25.tokens : [])" :key="i">{{ token }}</span></div><p v-if="bm25.terms_truncated" class="trace-warning">分词或逐词说明已截断，当前贡献项不是完整总分。</p><div class="trace-formula">IDF = ln(1 + (N − df + 0.5) / (df + 0.5))<br>分数 = Σ IDF × tf × (k₁ + 1) / (tf + k₁ × (1 − b + b × 长度 / 平均长度))</div><p class="trace-note">N={{ bm25.n_docs }} · avgdl={{ number(bm25.avgdl, 2) }} · k₁={{ bm25.k1 }} · b={{ bm25.b }} · minimum_should_match={{ debug.minimum_should_match ?? 1 }}</p></template>
              <template v-if="stage === 'dense'"><p class="trace-note">Embedding adapter 添加实际 query 前缀，把查询转成向量。FAISS FlatIP 扫描全索引，先保留本次 scope 内 ID，再取 Dense Top-K；归一化向量的内积可解释为余弦相似度。</p><div class="trace-facts"><div><small>模型</small><code>{{ manifest.model_id }} @ {{ manifest.revision }}</code></div><div><small>维度 / 归一化 / Pooling</small><code>{{ index.dimension }} / {{ manifest.normalize }} / {{ manifest.pooling }}</code></div><div><small>查询前缀</small><code>{{ json(manifest.query_prefix) }}</code></div><div><small>文档前缀</small><code>{{ json(manifest.passage_prefix) }}</code></div><div><small>实际编码输入（前缀由 adapter 添加）</small><code>{{ manifest.query_prefix }}{{ view === 'english' ? queryPlan.english : queryPlan.original }}</code></div><div><small>索引总向量 / scope 内向量</small><code>{{ index.total_vectors }} / {{ index.scoped_vectors }}</code></div></div></template>
              <template v-if="stage === 'rrf'"><p class="trace-note">不相加 BM25 和余弦原始分数。对每个实际融合列表，命中则贡献 1 / (k + 名次)，缺席贡献 0；名次从 1 开始。同分按最佳名次，再按 FAISS ID 排序。</p><div class="trace-formula">RRF(chunk) = Σ 1 / ({{ debug.rrf_k }} + rank) · {{ debug.fusion_strategy }}</div></template>
              <template v-if="stage === 'rerank'"><p class="trace-note">Cross-encoder 输入是实际问题 + 每条候选的 retrieval_content，输出用于相对排序，不是正确率或可信概率。排序后执行去重 / cell coverage / 文档均衡，再做表格上下文扩展。</p><div class="trace-facts"><div><small>精排模型</small><code>{{ reranker.model_id }} @ {{ reranker.revision }}</code></div><div><small>实际问题</small><code>{{ debug.rerank_query }}</code></div><div><small>输入数 / 模型长度上限</small><code>{{ debug.rerank_input_count }} / {{ reranker.max_tokens }}</code></div><div><small>选择策略</small><code>{{ record(debug.policy).selection }} · {{ record(debug.policy).document_balance }}</code></div></div><p v-if="reranker.status === 'unavailable'" class="trace-warning">精排不可用，保留融合候选顺序和原有分数。下表分数不是 cross-encoder 分数。</p></template>
              <p class="trace-score-note">{{ stage === 'bm25' ? 'BM25 分数：词项相关性' : stage === 'dense' ? 'Dense 分数：向量内积' : stage === 'rrf' ? 'RRF 分数：名次贡献之和' : reranker.status === 'unavailable' ? '降级分数：保留输入候选的原分数' : '精排分数：模型相关性输出' }}。不同阶段的分数不可直接比较。</p>
              <p class="trace-note">配置候选深度：Dense {{ record(debug.policy).dense_top_k ?? '—' }} / BM25 {{ record(debug.policy).bm25_top_k ?? '—' }} / RRF {{ record(debug.policy).rrf_top_k ?? '—' }}。多文档均衡可能为缺席论文追加候选，所以实际数量可能超过配置深度；以本轮记录为准。</p>
              <div class="trace-table-toolbar"><strong>{{ ranked.length }} 个阶段候选 <small>· 筛选后 {{ visibleRows.length }}</small></strong><input v-model="filter" placeholder="筛选论文、文本或 ID" aria-label="筛选候选"></div>
              <div class="trace-table-scroll"><table class="trace-candidate-table"><thead><tr><th>名次</th><th>候选片段 · 点击查看</th><th>本阶段分数</th><th>Dense / BM25</th><th>RRF → 精排</th><th>选择</th></tr></thead><tbody><tr v-for="row in visibleRows" :key="row.id" :class="{ active: selectedRow?.id === row.id }"><td>{{ row.rank }}</td><td><button @click="focus = row.id"><strong>{{ candidate(row.id)?.document_title ?? '正文不可用 / 未加载' }}</strong><small>FAISS {{ row.id }} · {{ candidate(row.id)?.page_start == null ? '页码未知' : `第 ${candidate(row.id)?.page_start} 页` }} · {{ candidate(row.id)?.section_path.join(' › ') || '未识别章节' }}</small></button></td><td class="trace-score">{{ number(row.score) }}</td><td>{{ rankAt('dense', row) }} / {{ rankAt('bm25', row) }}</td><td>{{ rankAt('rrf', row) }} → {{ rankAt('rerank', row) }}</td><td><span :class="{ 'trace-kept': selected(row.id) }">{{ selected(row.id) ? '保留' : '未保留' }}</span></td></tr></tbody></table></div><p v-if="!visibleRows.length" class="trace-empty">{{ ranked.length ? '没有匹配筛选条件的候选。' : '这个阶段没有返回候选。' }}</p>
              <p v-if="active.candidates_truncated || active.unavailable_candidates" class="trace-warning">候选正文最多加载 200 个。{{ active.unavailable_candidates }} 个片段因来源删除、版本不匹配或存储清理而不可用；不使用其他版本替代。</p>
              <section v-if="selectedRow" class="trace-candidate-detail"><h3>候选 FAISS {{ selectedRow.id }} 的路径</h3><div class="trace-rank-path">Dense #{{ rankAt('dense', selectedRow) }} <span>＋</span> BM25 #{{ rankAt('bm25', selectedRow) }} <span>→</span> RRF #{{ rankAt('rrf', selectedRow) }} <span>→</span> 精排 #{{ rankAt('rerank', selectedRow) }} <span>→</span> {{ selected(selectedRow.id) ? '最终检索保留' : '未进入最终检索结果' }}</div>
                <template v-if="stage === 'bm25'"><p>片段长度 {{ bm25Candidate.doc_len ?? '—' }} tokens · 实际逐词贡献</p><div class="trace-table-scroll"><table><thead><tr><th>词</th><th>tf（片段词频）</th><th>df（含词片段数）</th><th>IDF</th><th>贡献</th></tr></thead><tbody><tr v-for="term in termRows" :key="String(term.term)"><td>{{ term.term }}</td><td>{{ term.tf }}</td><td>{{ term.df }}</td><td>{{ number(term.idf) }}</td><td>{{ number(term.contribution) }}</td></tr></tbody></table></div><p v-if="!termRows.length">本次没有保存此候选的 BM25 逐词说明。</p></template>
                <div v-if="stage === 'rrf'" class="trace-rrf-parts"><div v-for="part in rrfParts" :key="part.name"><span>{{ part.name }}</span><code>{{ part.rank ? `1 / (${debug.rrf_k} + ${part.rank})` : '未命中 → 0' }}</code><strong>{{ part.value.toFixed(8) }}</strong></div><div><b>总和</b><strong>{{ rrfSum.toFixed(8) }}</strong></div></div>
                <template v-if="selectedCandidate"><p class="trace-provenance">Chunk {{ selectedCandidate.chunk_id }}<br>Document {{ selectedCandidate.document_id }}</p><div class="trace-excerpts"><div><h4>raw_content · 引用与最终回答使用</h4><pre>{{ selectedCandidate.raw_content }}</pre><small v-if="selectedCandidate.raw_truncated">此预览截取前 1200 字符。</small></div><div><h4>retrieval_content · Embedding / BM25 / 精排使用</h4><pre>{{ selectedCandidate.retrieval_content }}</pre><small v-if="selectedCandidate.retrieval_truncated">此预览截取前 1200 字符，精排输入还受模型长度限制。</small></div></div></template>
              </section>
              <details v-if="stage === 'rerank'"><summary>全部选择原因与表格扩展记录</summary><p v-for="(decision, i) in decisions" :key="i"><code>{{ decision.chunk_id }}</code> · {{ decision.reason }} · 覆盖来源 {{ json(decision.covered_by) }}</p><pre>{{ json(debug.table_expansions) }}</pre></details>
              <details class="trace-timings"><summary>本次检索耗时明细 · GPU 等待 {{ number(debug.gpu_wait_ms, 1) }}ms</summary><div v-for="[name, value] in timings" :key="name"><span>{{ timingLabels[name] ?? name }}</span><code>{{ number(value, 2) }} ms</code></div><p>total 不含 GPU 等待；scope 包含快照、模型就绪和 scope_db，Dense / BM25 包含各自文件加载，汇总项与子项不应累加。旧记录未细分的阶段不补算。</p></details><details><summary>本次实际检索参数与完整调试数据</summary><pre>{{ json(debug) }}</pre></details>
            </template>
          </section>
        </div>
      </div>
      <footer class="trace-footer">运行 {{ data.run_id }} · 可观察执行记录，不包含隐藏思维链 · 详细记录按 Agent 保留期限清理</footer>
    </template>
  </section>
</template>
