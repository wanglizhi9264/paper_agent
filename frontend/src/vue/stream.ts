import { onScopeDispose, ref, watch, type Ref } from 'vue';
import { useQueryClient } from '@tanstack/vue-query';
import { parseAgentEvent, type AgentEvent } from '../types/agent';

export const terminal = (status: string) => ['succeeded', 'failed', 'cancelled'].includes(status);
export function useRunStream(id: Ref<string | null>) {
  const answer = ref('');
  const events = ref<AgentEvent[]>([]);
  const connection = ref('idle');
  const qc = useQueryClient();
  let source: EventSource | null = null;
  const stop = () => { source?.close(); source = null; };
  watch(id, run => {
    stop(); answer.value = ''; events.value = []; connection.value = run ? 'connecting' : 'idle';
    if (!run) return;
    let cursor = 0;
    const base = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';
    const stream = new EventSource(`${base}/api/v1/agent/runs/${run}/events`);
    source = stream;
    stream.onopen = () => { connection.value = 'connected'; };
    stream.onerror = () => { connection.value = 'reconnecting'; void qc.invalidateQueries({ queryKey: ['vue-run', run] }); };
    const receive = (event: MessageEvent<string>) => {
      let item: AgentEvent | null;
      try { item = parseAgentEvent(event.data); } catch { return; }
      if (!item || item.sequence <= cursor) return;
      cursor = item.sequence;
      events.value = [...events.value, item].slice(-100);
      if (['answer.reset', 'answer.started'].includes(item.type)) answer.value = '';
      if (item.type === 'answer.delta' && typeof item.payload.text === 'string') answer.value += item.payload.text;
      if (item.type === 'answer.completed' && typeof item.payload.answer === 'string') answer.value = item.payload.answer;
      if (['tool.completed', 'run.interrupted', 'run.completed', 'run.failed', 'run.cancelled'].includes(item.type)) {
        void qc.invalidateQueries({ queryKey: ['vue-run', run] });
      }
      if (['run.completed', 'run.failed', 'run.cancelled'].includes(item.type)) { stream.close(); connection.value = 'complete'; }
    };
    ['run.created', 'run.started', 'run.resumed', 'skill.loaded', 'action.selected', 'action.rejected', 'model.started', 'tool.started', 'tool.completed', 'context.compacted', 'budget.stopped', 'answer.started', 'answer.reset', 'answer.delta', 'answer.completed', 'run.interrupted', 'run.failed', 'run.cancelled', 'run.completed'].forEach(name => stream.addEventListener(name, receive));
  }, { immediate: true });
  onScopeDispose(stop);
  return { answer, events, connection };
}
