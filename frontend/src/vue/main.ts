import { createApp, defineAsyncComponent } from 'vue';
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query';
import Workspace from './Workspace.vue';
import './workspace.css';

const root = window.location.pathname === '/trace' ? defineAsyncComponent(() => import('./TracePage.vue')) : Workspace;
createApp(root).use(VueQueryPlugin, {
  queryClient: new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 10_000, refetchOnWindowFocus: false } } }),
}).mount('#root');
