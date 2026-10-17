import { flushPromises, mount } from '@vue/test-utils';
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Workspace from './Workspace.vue';
import { renderAnswer } from './markdown';
import type { AgentRun } from '../types/agent';

class FakeEvents {
  static instances: FakeEvents[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (event: MessageEvent<string>) => void>();
  close = vi.fn();
  constructor() { FakeEvents.instances.push(this); }
  addEventListener(name: string, fn: (event: MessageEvent<string>) => void) { this.listeners.set(name, fn); }
  emit(type: string, sequence: number, payload: Record<string, unknown>) { this.listeners.get(type)?.(new MessageEvent(type, { data: JSON.stringify({ type, sequence, payload }) })); }
}
const result: AgentRun = { run_id: 'run1', session_id: 'session1', snapshot_id: 'snap', model_id: 'test', status: 'succeeded', events_url: '', usage: { llm_calls: 2, tool_calls: 1, search_calls: 1, tokens: 200 }, selected_skills: {}, stop_reason: 'finished', error_code: null, final_message_id: 'message', answer: '实验结果 [1]', sources: [{ index: 1, chunk_id: 'chunk-real', document_id: 'doc1', document_title: 'Example paper', section_path: ['Results'], page: '8', page_start: 8, page_end: 8, element_id: null, element_kind: null, cell_ids: [], bboxes: [], content: 'Verified original excerpt', truncated: false }], claims: [], steps: [], created_at: new Date().toISOString() };
const mounted: ReturnType<typeof mount>[] = [];
let requests: { path: string; body: unknown }[];
function setup(run: AgentRun = result, fail = false) {
  vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
    if (fail) throw new Error('连接失败');
    requests.push({ path, body: options?.body ? JSON.parse(String(options.body)) as unknown : null });
    const body = path.includes('/skills') ? [{ name: 'experiment-audit', description: '核验', digest: 'hash' }]
      : path.includes('/documents') ? { items: [{ id: 'doc1', title: 'Example paper', status: 'ready', page_count: 12 }], next_cursor: null }
      : path.endsWith('/sessions') ? { id: 'session1' } : run;
    return new Response(JSON.stringify(body), { status: 200 });
  }));
  const wrapper = mount(Workspace, { global: { plugins: [[VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }) }]] } });
  mounted.push(wrapper);
  return wrapper;
}
beforeEach(() => { localStorage.clear(); requests = []; FakeEvents.instances = []; vi.stubGlobal('EventSource', FakeEvents); });
afterEach(() => { mounted.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals(); });
describe('Vue research workspace', () => {
  it('starts an agent run with selected paper scope and chosen skill', async () => {
    const w = setup(); await flushPromises();
    await w.find('.scope-heading button').trigger('click');
    await w.find('input[type=checkbox]').setValue(true);
    await w.find('select').setValue('experiment-audit');
    await w.find('textarea').setValue('请核对实验准确率');
    await w.find('form').trigger('submit'); await flushPromises();
    expect(requests.find(r => r.path.endsWith('/sessions'))?.body).toMatchObject({ scope: { type: 'documents', document_ids: ['doc1'] } });
    expect(requests.find(r => r.path.endsWith('/agent/runs'))?.body).toMatchObject({ query: '请核对实验准确率', mode: 'agent', skill_names: ['experiment-audit'] });
    expect(localStorage.getItem('paper-agent-run')).toBe('run1');
    await w.find('.source-card').trigger('click');
    expect(w.find('[role=dialog]').text()).toContain('Verified original excerpt');
    expect(w.find('[role=dialog]').text()).toContain('chunk-real');
  });
  it('explains evidence gaps in Chinese without claiming the paper has no answer', async () => {
    localStorage.setItem('paper-agent-run', 'run1');
    const w = setup({ ...result, answer: 'Insufficient evidence in the provided sources.' }); await flushPromises();
    expect(w.text()).toContain('这不代表论文中一定没有答案');
    expect(w.text()).not.toContain('Insufficient evidence');
  });
  it('deduplicates replayed deltas, resets attempts and closes the SSE connection', async () => {
    localStorage.setItem('paper-agent-run', 'run1');
    const w = setup({ ...result, status: 'running', answer: null, sources: [] }); await flushPromises();
    const stream = FakeEvents.instances[0]; expect(stream).toBeDefined();
    stream?.emit('answer.delta', 1, { text: '旧' }); stream?.emit('answer.delta', 1, { text: '旧' });
    stream?.emit('answer.reset', 2, {}); stream?.emit('answer.delta', 3, { text: '新回答' }); await flushPromises();
    expect(w.find('.prose').text()).toBe('新回答');
    w.unmount(); mounted.pop(); expect(stream?.close).toHaveBeenCalled();
  });
  it('renders dependency failure and keeps send disabled', async () => {
    const w = setup(result, true); await flushPromises();
    expect(w.find('[role=alert]').text()).toContain('连接失败');
    expect(w.find('[aria-label=发送问题]').attributes('disabled')).toBeDefined();
  });
  it('hides unvalidated streamed output when the run fails citation validation', async () => {
    localStorage.setItem('paper-agent-run', 'run1');
    const w = setup({ ...result, status: 'failed', error_code: 'CITATION_INVALID', answer: null, sources: [] });
    await flushPromises();
    FakeEvents.instances[0]?.emit('answer.delta', 1, { text: 'Unvalidated statement [99]' });
    await flushPromises();
    expect(w.find('.prose').exists()).toBe(false);
    expect(w.find('[role=alert]').text()).toContain('CITATION_INVALID');
  });
  it('does not execute paper HTML or render remote image/link navigation', () => {
    const html = renderAnswer('<script>alert(1)</script> ![image](https://example.com/image) [link](javascript:alert(1))');
    expect(html).not.toContain('<script>'); expect(html).not.toContain('<img'); expect(html).not.toContain('href=');
  });
});
