<script setup lang="ts">
import { computed, onScopeDispose, ref } from 'vue';
import { useMutation, useQuery } from '@tanstack/vue-query';
import { apiGet, apiPost } from '../api/client';
import type { DocumentListResponse } from '../types/document';
import type { AgentRun, AgentSkill } from '../types/agent';
import type { SessionItem } from '../types/chat';
import { terminal, useRunStream } from './stream';
import { renderAnswer } from './markdown';

const runId = ref(localStorage.getItem('paper-agent-run'));
const sessionId = ref(localStorage.getItem('paper-agent-session'));
const question = ref('');
const submitted = ref(localStorage.getItem('paper-agent-question') ?? '');
const selected = ref<string[]>([]);
const skill = ref('');
const showLibrary = ref(false);
const showDetails = ref(false);
const selectedSource = ref<number | null>(null);
const now = ref(Date.now());
const timer = window.setInterval(() => { now.value = Date.now(); }, 1000);
onScopeDispose(() => window.clearInterval(timer));
const docs = useQuery({ queryKey: ['vue-documents'], queryFn: () => apiGet<DocumentListResponse>('/api/v1/documents?limit=100'), refetchInterval: 30_000 });
const skills = useQuery({ queryKey: ['vue-skills'], queryFn: () => apiGet<AgentSkill[]>('/api/v1/agent/skills'), retry: false });
const run = useQuery({ queryKey: computed(() => ['vue-run', runId.value]), queryFn: () => apiGet<AgentRun>(`/api/v1/agent/runs/${runId.value}`), enabled: computed(() => Boolean(runId.value)), refetchInterval: q => q.state.data && terminal(q.state.data.status) ? false : 2000 });
const stream = useRunStream(runId);
const busy = computed(() => Boolean(run.data.value && !terminal(run.data.value.status)));
const readyDocs = computed(() => docs.data.value?.items.filter(d => d.status === 'ready') ?? []);
const sources = computed(() => run.data.value?.sources ?? []);
const answer = computed(() => {
  if (['failed', 'cancelled'].includes(run.data.value?.status ?? '')) return '';
  return run.data.value?.answer || stream.answer.value;
});
const insufficient = computed(() => answer.value.startsWith('Insufficient evidence in the provided sources.'));
const html = computed(() => renderAnswer(answer.value));
const currentSource = computed(() => sources.value.find(s => s.index === selectedSource.value));
const elapsed = computed(() => {
  const created = run.data.value?.created_at;
  if (!created) return 0;
  return Math.max(0, Math.floor((now.value - Date.parse(created)) / 1000));
});
const statusLabels: Record<string, string> = { queued: '等待执行', running: '正在分析', interrupted: '执行已中断，可继续', succeeded: '分析完成', failed: '分析失败', cancelled: '已取消' };
const toolLabels: Record<string, string> = { list_papers: '定位相关论文', search_papers: '检索原文证据', read_chunks: '核对原文段落', read_table: '读取实验表格', load_skill: '加载阅读技能', read_skill_resource: '读取技能资料', finish: '整理结论' };
const skillLabels: Record<string, string> = { 'paper-reading': '精读论文', 'method-analysis': '方法分析', 'experiment-audit': '实验核验', 'paper-comparison': '论文对比' };
const start = useMutation({ mutationFn: async () => {
  const query = question.value.trim();
  if (!query) throw new Error('请先输入问题');
  let session = sessionId.value;
  if (!session) {
    const value = await apiPost<SessionItem>('/api/v1/sessions', { title: query.slice(0, 80), scope: selected.value.length ? { type: 'documents', document_ids: selected.value } : { type: 'all' } });
    session = value.id; sessionId.value = session; localStorage.setItem('paper-agent-session', session);
  }
  const result = await apiPost<AgentRun>('/api/v1/agent/runs', { session_id: session, query, mode: 'agent', skill_names: skill.value ? [skill.value] : [] }, { 'Idempotency-Key': crypto.randomUUID() });
  submitted.value = query; localStorage.setItem('paper-agent-question', query);
  return result;
}, onSuccess: value => { runId.value = value.run_id; localStorage.setItem('paper-agent-run', value.run_id); question.value = ''; selectedSource.value = null; showLibrary.value = false; } });
const control = useMutation({ mutationFn: (action: 'cancel' | 'resume') => apiPost<AgentRun>(`/api/v1/agent/runs/${runId.value}/${action}`, {}, { 'Idempotency-Key': crypto.randomUUID() }), onSuccess: () => { void run.refetch(); } });
const error = computed(() => start.error.value ?? control.error.value ?? run.error.value ?? skills.error.value ?? docs.error.value);
function newSession() {
  if (busy.value || start.isPending.value) return;
  runId.value = null; sessionId.value = null; submitted.value = ''; selectedSource.value = null;
  ['paper-agent-run', 'paper-agent-session', 'paper-agent-question'].forEach(key => localStorage.removeItem(key));
  start.reset(); control.reset();
}
function cancel() { if (window.confirm('取消正在执行的分析？已完成的步骤会保留。')) control.mutate('cancel'); }
const examples = ['这篇论文的方法解决了什么问题？', '核对实验表格中的数据集、指标和结果', '比较两篇论文的方法与实验条件'];
</script>

<template>
  <div class="research-app">
    <aside class="sidebar">
      <a href="/" class="brand"><span class="brand-mark">P<span>·</span></span><span>Paper<span class="brand-light">space</span><small>你的论文研究工作台</small></span></a>
      <button class="new-chat" :disabled="busy || start.isPending.value" @click="newSession"><span>＋</span> 新建研究</button>
      <div class="nav-caption">工作空间</div>
      <nav aria-label="主导航">
        <a class="nav-item active" href="/agent"><span>◈</span> 论文问答 <small>AGENT</small></a>
        <a class="nav-item" href="/documents"><span>▤</span> 论文库 <b>{{ readyDocs.length }}</b></a>
        <a class="nav-item" href="/collections"><span>▧</span> 我的合集</a>
      </nav>
      <div class="scope-heading"><span class="nav-caption">{{ sessionId ? "库中论文" : "本次研究范围" }}</span><button class="text-button" :disabled="!!sessionId" @click="showLibrary = !showLibrary">{{ showLibrary ? '收起' : '选择' }}</button></div>
      <p class="scope-note">{{ sessionId ? '会话范围已固定，新建研究可重新选择。' : selected.length ? `已选择 ${selected.length} 篇论文` : '全部已就绪论文' }}</p>
      <div v-if="docs.isLoading.value" class="side-empty" role="status">正在读取论文库…</div>
      <div v-else-if="!readyDocs.length" class="side-empty">还没有可用论文。<a href="/documents">上传第一篇 →</a></div>
      <div class="paper-list">
        <label v-for="doc in readyDocs" :key="doc.id" class="paper-row"><input v-if="showLibrary && !sessionId" v-model="selected" type="checkbox" :value="doc.id"><span class="file-icon">PDF</span><span :title="doc.title || doc.filename">{{ doc.title || doc.filename }}<small>{{ doc.page_count ?? '—' }} 页 · 已就绪</small></span></label>
      </div>
      <div class="sidebar-bottom"><span class="local-dot"></span> 本地论文库 <a href="/health" title="查看服务状态">服务状态 ↗</a></div>
    </aside>
    <main class="workspace">
      <header class="topbar"><span>研究工作台 <span class="breadcrumb">/</span> <strong>论文问答</strong></span><span class="topbar-note">基于原文 · 可追溯证据</span></header>
      <div class="workspace-body" :class="{ 'has-answer': !!runId }">
        <section v-if="!runId" class="welcome">
          <div class="eyebrow"><span class="tiny-mark">✳</span> READ. QUESTION. DISCOVER.</div>
          <h1>从一个问题，<br><span>读懂一篇论文。</span></h1>
          <p>让研究回到内容本身。检索原文、核对实验，<br>每一个结论，都有证据可循。</p>
          <div class="suggestions"><button v-for="(example, i) in examples" :key="example" @click="question = example"><span>{{ ['◎', '▦', '⇄'][i] }}</span><strong>{{ ['理解方法', '核对实验', '对比研究'][i] }}</strong><small>{{ example }}</small><b>↗</b></button></div>
        </section>
        <section v-else class="conversation" aria-live="polite">
          <div class="question-bubble" v-if="submitted">{{ submitted }}</div>
          <div class="answer-heading"><span class="answer-symbol">✳</span><div><strong>Paper Assistant</strong><small>{{ statusLabels[run.data.value?.status ?? 'queued'] }}</small></div><span v-if="busy" class="duration">{{ elapsed }}s</span></div>
          <div v-if="run.isLoading.value" role="status" class="progress-card">正在恢复研究记录…</div>
          <div v-if="run.data.value" class="progress-card">
            <button class="progress-toggle" @click="showDetails = !showDetails"><span :class="{ pulse: busy }">{{ busy ? '●' : '✓' }}</span><strong>{{ statusLabels[run.data.value.status] }}</strong><small>{{ run.data.value.usage.search_calls }} 次检索 · {{ sources.length }} 条来源</small><span>{{ showDetails ? '−' : '＋' }}</span></button>
            <ol v-if="showDetails" class="steps"><li v-for="step in run.data.value.steps" :key="step.sequence"><span>{{ step.status === 'running' ? '◌' : step.status === 'failed' ? '!' : '✓' }}</span>{{ toolLabels[step.tool] ?? step.tool }}<small>{{ step.status === 'running' ? '进行中' : step.status === 'failed' ? '失败' : '完成' }}</small></li><li v-if="!run.data.value.steps.length">等待 worker 接收任务…</li></ol>
            <p v-if="busy && elapsed > 20" class="wait-hint">仍在处理。首次加载本地模型可能较慢，可以展开查看执行步骤。</p>
          </div>
          <div v-if="insufficient" class="evidence-gap"><strong>本次检索还不足以支持答案</strong><p>没有拿到与所问方法、数据集和指标直接对应的证据。可选择具体论文，或补充方法名、ImageNet 子集及指标后重试。</p><small>这不代表论文中一定没有答案。</small></div>
          <article v-else-if="answer" class="prose" v-html="html"></article>
          <div v-else-if="busy" class="thinking" role="status"><i></i><i></i><i></i><span>{{ stream.events.value.some(e => e.type === 'answer.started') ? '正在撰写回答' : '正在查找并核对论文证据' }}</span></div>
          <div v-if="run.data.value?.status === 'failed'" class="error-banner" role="alert">分析未能完成：{{ run.data.value.error_code }}。执行记录已保留。</div>
          <div v-if="sources.length" class="source-section"><div class="section-label">原文证据 <span>{{ sources.length }}</span></div><div class="source-grid"><button v-for="s in sources" :key="s.chunk_id" class="source-card" @click="selectedSource = s.index"><span class="source-number">{{ s.index }}</span><strong>{{ s.document_title }}</strong><small>第 {{ s.page }} 页 <span>查看原文 ↗</span></small></button></div></div>
          <details v-if="run.data.value" class="run-diagnostics"><summary>运行详情</summary><p>模型 {{ run.data.value.model_id }} · 模型调用 {{ run.data.value.usage.llm_calls }} · tokens {{ run.data.value.usage.tokens }} · {{ run.data.value.stop_reason || '执行中' }} · SSE {{ stream.connection.value }}</p><p v-for="step in stream.events.value.filter(e => e.type === 'tool.completed' && e.payload.error_code)" :key="step.sequence">工具错误：{{ step.payload.error_code }}</p></details>
        </section>
        <div class="composer-area">
          <p v-if="error" role="alert" class="error-banner">{{ error.message }} <button class="text-button" @click="skills.refetch(); docs.refetch(); run.refetch()">重新连接</button></p>
          <form class="composer" @submit.prevent="start.mutate()">
            <label class="sr-only" for="research-question">研究问题</label><textarea id="research-question" v-model="question" :disabled="busy || start.isPending.value" placeholder="向你的论文提问，或继续追问…" rows="3" @keydown.ctrl.enter.prevent="!busy && !start.isPending.value && question.trim() && start.mutate()"></textarea>
            <div class="composer-toolbar"><button type="button" class="scope-chip" :disabled="!!sessionId" @click="showLibrary = !showLibrary">▤ {{ sessionId ? '固定会话范围' : selected.length ? `${selected.length} 篇论文` : '全部论文' }}</button><label class="skill-picker"><span class="sr-only">阅读技能</span><select v-model="skill" :disabled="busy"><option value="">✳ 自动选择技能</option><option v-for="s in skills.data.value" :key="s.name" :value="s.name">{{ skillLabels[s.name] ?? s.name }}</option></select></label><span class="toolbar-spacer"></span><button v-if="run.data.value?.status === 'interrupted'" type="button" class="resume-button" :disabled="control.isPending.value" @click="control.mutate('resume')">继续执行</button><button v-if="busy" type="button" class="send-button cancel-button" :disabled="control.isPending.value" @click="cancel" aria-label="取消分析">■</button><button v-else class="send-button" :disabled="!question.trim() || start.isPending.value || !skills.data.value" aria-label="发送问题">{{ start.isPending.value ? '…' : '↑' }}</button></div>
          </form><p class="composer-footnote">回答基于所选论文，数值与结论请结合原文核验。<span>Ctrl + Enter 发送</span></p>
        </div>
      </div>
    </main>
    <div v-if="currentSource" class="drawer-backdrop" @click.self="selectedSource = null"><aside class="source-drawer" role="dialog" aria-modal="true" aria-labelledby="source-title" @keydown.esc="selectedSource = null" tabindex="-1"><div class="drawer-top"><span>原文证据 / {{ currentSource.index }}</span><button aria-label="关闭原文" @click="selectedSource = null">×</button></div><h2 id="source-title">{{ currentSource.document_title }}</h2><p class="drawer-meta">第 {{ currentSource.page }} 页 · {{ currentSource.section_path.join(' › ') }}</p><pre>{{ currentSource.content }}</pre><p v-if="currentSource.truncated" class="drawer-meta">此片段按上下文预算截断。</p><code>Chunk {{ currentSource.chunk_id }}</code></aside></div>
  </div>
</template>
