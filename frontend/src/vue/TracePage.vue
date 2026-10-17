<script setup lang="ts">
import { ref } from 'vue';
import { useMutation, useQuery } from '@tanstack/vue-query';
import { apiGet, apiPost } from '../api/client';
import type { AgentRun } from '../types/agent';
import type { SessionItem } from '../types/chat';
import type { DocumentListResponse } from '../types/document';
import TraceView from './TraceView.vue';
import './trace.css';

const runId = ref(new URLSearchParams(window.location.search).get('run') ?? '');
const question = ref('MDM 的 FID 是多少？');
const selected = ref<string[]>([]);
const docs = useQuery({ queryKey: ['trace-documents'], queryFn: () => apiGet<DocumentListResponse>('/api/v1/documents?limit=100') });
const start = useMutation({ mutationFn: async () => {
  const query = question.value.trim();
  if (!query) throw new Error('请输入问题');
  const session = await apiPost<SessionItem>('/api/v1/sessions', { title: query.slice(0, 80), scope: selected.value.length ? { type: 'documents', document_ids: selected.value } : { type: 'all' } });
  return apiPost<AgentRun>('/api/v1/agent/runs', { session_id: session.id, query, mode: 'agent', trace_enabled: true }, { 'Idempotency-Key': crypto.randomUUID() });
}, onSuccess: run => {
  runId.value = run.run_id;
  history.replaceState(null, '', `/trace?run=${encodeURIComponent(run.run_id)}`);
} });
</script>

<template>
  <main class="trace-page">
    <header class="trace-header"><a href="/agent">← 论文问答</a><span>Paper<span class="trace-brand-light">space</span> <b>/</b> 执行链路</span><span class="trace-local">本地运行记录</span></header>
    <section class="trace-intro"><span class="trace-eyebrow">RETRIEVAL EXPLORER</span><h1>看清一个答案是怎样找到的。</h1><p>从用户问题到原文引用，逐步检查实际输入、候选、分数和耗时。</p></section>
    <form class="trace-question" @submit.prevent="start.mutate()">
      <label for="trace-question">运行一个问题</label><div><input id="trace-question" v-model="question" :disabled="start.isPending.value" maxlength="4000"><button :disabled="!question.trim() || start.isPending.value">{{ start.isPending.value ? '正在创建…' : '运行并查看链路 →' }}</button></div>
      <details><summary>选择检索范围 · {{ selected.length ? `${selected.length} 篇` : '全部已就绪论文' }}</summary><p v-if="docs.isLoading.value">正在加载论文…</p><p v-else-if="docs.error.value" role="alert">{{ docs.error.value.message }}</p><p v-else-if="!docs.data.value?.items.some(d => d.status === 'ready')">没有可用论文，<a href="/documents">先上传论文</a>。</p><label v-for="doc in docs.data.value?.items.filter(d => d.status === 'ready')" :key="doc.id"><input v-model="selected" type="checkbox" :value="doc.id">{{ doc.title || doc.filename }}</label></details>
      <p>使用与问答页相同的 Agent，可能产生模型调用费用。每次创建独立会话；运行中可在下方取消。</p>
      <p v-if="start.error.value" role="alert" class="trace-warning">{{ start.error.value.message }}</p>
    </form>
    <TraceView v-if="runId" :run-id="runId" />
    <section v-else class="trace-empty"><span>⌁</span><h2>从一个真实问题开始</h2><p>输入问题后运行，或者从问答页的“查看完整执行链路”打开已有记录。</p><p>这里展示实际工具调用与检索数据，不展示模型隐藏思维链。</p></section>
  </main>
</template>
