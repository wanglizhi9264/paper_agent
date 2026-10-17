import { flushPromises, mount } from '@vue/test-utils';
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import TraceView from './TraceView.vue';
import TracePage from './TracePage.vue';
import { contributions, type AgentTrace } from '../types/trace';

const fixture: AgentTrace = {
  schema_version: 1, run_id: 'run1', query: 'MDM 的 FID 是多少？', status: 'succeeded', availability: 'recorded',
  created_at: '2026-10-17T00:00:00Z', finished_at: '2026-10-17T00:00:10Z', snapshot_id: 'snapshot1', scope: { type: 'documents', document_ids: ['doc1'] }, model_id: 'test-model', context_limit: 4096,
  loaded_skills: { 'experiment-audit': 'digest' }, usage: { llm_calls: 2, tool_calls: 1, tokens: 1200 }, stop_reason: 'completed', error_code: null, answer: 'Test answer [1].', observed_evidence_count: 2,
  context: { input_tokens: 900, output_reserved_tokens: 1500, safety_margin_tokens: 512, history_messages_received: 0 },
  events: [{ sequence: 1, type: 'tool.started', timestamp: '2026-10-17T00:00:02Z', payload: { tool: 'search_papers' } }],
  sources: [{ index: 1, chunk_id: 'c12', document_id: 'doc1', document_title: 'Test paper', section_path: ['Results'], page: '3', page_start: 3, page_end: 3, element_id: null, element_kind: null, cell_ids: [], bboxes: [], content: 'Verified test source', truncated: false }],
  steps: [{ sequence: 1, call_id: 'search1', tool: 'search_papers', status: 'succeeded', arguments: { query: 'MDM FID HumanML3D' }, started_at: '2026-10-17T00:00:02Z', completed_at: '2026-10-17T00:00:05Z', duration_ms: 3000, error_code: null, output: [{ chunk_id: 'c12', text: 'Test evidence' }], candidates_truncated: false, unavailable_candidates: 0,
    candidates: [11, 12].map(id => ({ chunk_id: `c${id}`, document_id: 'doc1', document_title: 'Test paper', section_path: ['Results'], page_start: 3, page_end: 3, raw_content: `original ${id} <script>alert(1)</script>`, retrieval_content: `retrieval ${id}`, raw_truncated: false, retrieval_truncated: false })),
    retrieval: {
      schema_version: 1, retrieval_queries: { original: 'MDM FID HumanML3D', english: null }, rerank_query: 'MDM FID HumanML3D',
      dense: [[11, .9], [12, .8]], dense_original: [[11, .9], [12, .8]], bm25: [[12, 4], [11, 2]], bm25_original: [[12, 4], [11, 2]], rrf: [[11, 1 / 61 + 1 / 62, 'dense'], [12, 1 / 61 + 1 / 62, 'bm25']], rerank: [[12, .95, 'rerank'], [11, .2, 'rerank']], selected: [[12, .95, 'rerank']], candidate_chunk_ids: { '11': 'c11', '12': 'c12' },
      fusion_lists: { dense: [[11, .9], [12, .8]], bm25: [[12, 4], [11, 2]] }, rrf_k: 60, fusion_strategy: 'standard_two_source_rrf',
      embedding_manifest: { model_id: 'test-embedding', revision: 'r1', dimension: 96, normalize: true, pooling: 'mean', query_prefix: 'query: ', passage_prefix: 'passage: ' }, index: { dimension: 96, total_vectors: 40, scoped_vectors: 2 },
      bm25_explanations: { original: { query: 'MDM FID HumanML3D', tokens: ['mdm', 'fid', 'humanml3d'], n_docs: 40, avgdl: 100, k1: 1.5, b: .75, candidates: { '12': { doc_len: 80, terms: [{ term: 'mdm', tf: 2, df: 4, idf: 2.2, contribution: 3.1 }] } } } },
      reranker: { model_id: 'test-reranker', revision: 'r2', status: 'applied', max_tokens: 512 }, policy: { selection: 'cell_coverage', document_balance: 'off' }, selection_decisions: [], table_expansions: {}, timings_ms: { dense: 20, bm25: 3, rerank: 200, total: 240 }, gpu_wait_ms: 12,
    } }],
};
const mounted: ReturnType<typeof mount>[] = [];
const plugins = (): [typeof VueQueryPlugin, { queryClient: QueryClient }][] => [[VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }) }]];
function setup(data: AgentTrace = fixture, failure = false) {
  vi.stubGlobal('fetch', vi.fn(async () => {
    if (failure) throw new Error('连接失败');
    return new Response(JSON.stringify(data), { status: 200 });
  }));
  const w = mount(TraceView, { props: { runId: 'run1' }, global: { plugins: plugins() } }); mounted.push(w); return w;
}
async function tab(w: ReturnType<typeof mount>, name: string) {
  const button = w.findAll('.trace-tabs button').find(b => b.text().includes(name));
  if (!button) throw new Error(`Missing tab ${name}`);
  await button.trigger('click');
}
afterEach(() => { mounted.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals(); history.replaceState(null, '', '/'); });

describe('actual retrieval trace', () => {
  it('distinguishes the original question from the actual agent query, without inventing translation', async () => {
    const w = setup(); await flushPromises();
    expect(w.text()).toContain('MDM 的 FID 是多少？'); expect(w.text()).toContain('MDM FID HumanML3D');
    expect(w.text()).toContain('这是 Agent 实际选择的查询'); expect(w.text()).toContain('未执行');
    expect(w.text()).toContain('3.00s');
    await tab(w, 'BM25');
    expect(w.find('.trace-token-list').text()).toContain('humanml3d');
    expect(w.text()).toContain('片段长度 80 tokens'); expect(w.text()).toContain('3.10000');
    await tab(w, 'FAISS'); expect(w.text()).toContain('96 / true / mean'); expect(w.text()).toContain('query: MDM FID HumanML3D');
    expect(w.text()).toContain('raw_content'); expect(w.text()).toContain('retrieval_content');
    expect(w.findAll('script')).toHaveLength(0); expect(w.text()).toContain('<script>alert(1)</script>');
    await tab(w, 'RRF'); expect(w.find('.trace-rrf-parts').text()).toContain('1 / (60 + 1)');
    expect(w.find('.trace-rrf-parts').text()).toContain((1 / 61 + 1 / 62).toFixed(8));
    await tab(w, '精排'); expect(w.find('.trace-candidate-table tbody tr').text()).toContain('0.95000');
    await w.findAll('.trace-candidate-table button')[1]?.trigger('click');
    expect(w.find('.trace-candidate-detail').text()).toContain('候选 FAISS 11');
    expect(w.find('.trace-candidate-detail').text()).toContain('未进入最终检索结果');
    await tab(w, '上下文'); expect(w.text()).toContain('Verified test source'); expect(w.text()).toContain('900');
    await tab(w, '事件'); expect(w.text()).toContain('+2.00s'); expect(w.text()).toContain('tool.started');
  });
  it('labels rerank failure and never presents retained fusion scores as cross-encoder scores', async () => {
    const data = structuredClone(fixture); const step = data.steps[0];
    if (!step?.retrieval) throw new Error('Missing fixture');
    step.retrieval.reranker = { status: 'unavailable' }; step.retrieval.rerank = step.retrieval.rrf;
    const w = setup(data); await flushPromises(); await tab(w, '精排');
    expect(w.text()).toContain('下表分数不是 cross-encoder 分数');
    expect(w.text()).toContain('降级分数');
  });
  it('handles old, expired, running, failed and empty traces explicitly', async () => {
    const w = setup({ ...fixture, availability: 'not_recorded', steps: [] }); await flushPromises();
    expect(w.text()).toContain('历史得分与搜索参数未记录');
    w.unmount(); mounted.pop();
    const expired = setup({ ...fixture, availability: 'expired', steps: [] }); await flushPromises();
    expect(expired.text()).toContain('详细链路已超过保留期限'); expired.unmount(); mounted.pop();
    const running = structuredClone(fixture); running.status = 'running'; running.finished_at = null;
    const first = running.steps[0]; if (!first) throw new Error('Missing fixture');
    first.status = 'running'; first.retrieval = null;
    const pending = setup(running); await flushPromises(); await tab(pending, 'FAISS');
    expect(pending.text()).toContain('检索正在执行'); pending.unmount(); mounted.pop();
    running.status = 'failed'; running.error_code = 'TOOL_TIMEOUT'; first.status = 'failed'; first.error_code = 'TOOL_TIMEOUT';
    const failed = setup(running); await flushPromises(); await tab(failed, 'BM25');
    expect(failed.text()).toContain('检索失败：TOOL_TIMEOUT');
  });
  it('renders connection failure with retry', async () => {
    const w = setup(fixture, true); await flushPromises(); expect(w.find('[role=alert]').text()).toContain('连接失败');
    expect(w.find('[role=alert] button').text()).toBe('重试');
  });
  it('uses every actual fusion list, with zero contribution when absent', () => {
    const parts = contributions({ rrf_k: 60, fusion_lists: { dense_original: [[11, .9]], dense_english: [[12, .8], [11, .6]], bm25_original: [], bm25_english: [[12, 5]] } }, 11);
    expect(parts.map(p => p.rank)).toEqual([1, 2, null, null]);
    expect(parts.reduce((n, p) => n + p.value, 0)).toBeCloseTo(1 / 61 + 1 / 62);
  });
  it('starts the same agent with trace enabled and explicit paper scope from the explorer', async () => {
    const requests: { path: string; body: unknown }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
      requests.push({ path, body: options?.body ? JSON.parse(String(options.body)) as unknown : null });
      const value = path.includes('/documents') ? { items: [{ id: 'doc1', title: 'Test paper', status: 'ready' }] }
        : path.endsWith('/sessions') ? { id: 'session1' } : path.endsWith('/agent/runs') ? { run_id: 'run1' } : fixture;
      return new Response(JSON.stringify(value), { status: 200 });
    }));
    const w = mount(TracePage, { global: { plugins: plugins() } }); mounted.push(w); await flushPromises();
    await w.find('input[type=checkbox]').setValue(true); await w.find('form').trigger('submit'); await flushPromises();
    expect(requests.find(r => r.path.endsWith('/sessions'))?.body).toMatchObject({ scope: { type: 'documents', document_ids: ['doc1'] } });
    expect(requests.find(r => r.path.endsWith('/agent/runs'))?.body).toMatchObject({ query: 'MDM 的 FID 是多少？', trace_enabled: true, session_id: 'session1' });
    expect(location.search).toBe('?run=run1');
  });
});
