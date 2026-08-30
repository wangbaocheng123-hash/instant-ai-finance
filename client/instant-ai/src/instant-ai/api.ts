import type {
  AppStatus, AuthLoginResult, AuthStatus, FinanceItem, FinanceItemDetail, ModelMrChatConfig, ModelMrChatResult,
  ModelMrStatus, ModelMrThoughtCategory, ModelMrTranscriptionResult, ModelMrWork, ModelMrWorkDetail,
  ReaderTranslationResult, SourceStatus,
  TranslationBatchResult, TranslationStatus,
  WatchEventsResponse,
} from './types';

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method === 'POST') {
    headers.set('Content-Type', 'application/json');
    headers.set('X-Instant-AI', '1');
  }
  const response = await fetch(path, { ...init, headers, cache: 'no-store' });
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/api/auth/')) {
      window.dispatchEvent(new CustomEvent('instant-ai-auth-required'));
    }
    const payload = await response.json().catch(() => ({})) as { error?: string };
    throw new Error(payload.error || `资讯接口请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const instantApi = {
  authStatus: () => request<AuthStatus>('/api/auth/status'),
  login: (username: string, password: string) => request<AuthLoginResult>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  logout: () => request<{ ok: boolean }>('/api/auth/logout', {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  modelMrStatus: () => request<ModelMrStatus>('/api/model-mr/status'),
  modelMrWorks: (limit = 40) => request<{ items: ModelMrWork[]; count: number }>(`/api/model-mr/works?limit=${limit}`),
  modelMrWork: (id: number) => request<ModelMrWorkDetail>(`/api/model-mr/works/${id}`),
  saveModelMrTitle: (id: number, title: string) => request<{ ok: boolean; title: string; saved: boolean; mode: string }>(`/api/model-mr/works/${id}/title`, {
    method: 'POST',
    body: JSON.stringify({ title }),
  }),
  saveModelMrVideoText: (id: number, text: string) => request<{ ok: boolean; text: string; saved: boolean; mode: string }>(`/api/model-mr/works/${id}/video-text`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  }),
  transcribeModelMrWork: (id: number, engine: 'video' | 'doubao') => request<ModelMrTranscriptionResult>(`/api/model-mr/works/${id}/${engine === 'doubao' ? 'doubao-transcribe' : 'transcribe'}`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  modelMrThoughts: () => request<{ categories: ModelMrThoughtCategory[]; count: number; purpose: string }>('/api/model-mr/thoughts'),
  modelMrChatConfig: () => request<ModelMrChatConfig>('/api/model-mr/chat/config'),
  modelMrChat: (messages: Array<{ role: 'user' | 'assistant'; content: string }>, model: string) => request<ModelMrChatResult>('/api/model-mr/chat', {
    method: 'POST',
    body: JSON.stringify({ messages, model }),
  }),
  status: () => request<AppStatus>('/api/status'),
  items: (topic = '', query = '', limit = 40) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (topic) params.set('topic', topic);
    if (query) params.set('q', query);
    return request<FinanceItem[]>(`/api/items?${params.toString()}`);
  },
  hot: (limit = 40) => request<FinanceItem[]>(`/api/hot?limit=${limit}`),
  watchEvents: () => request<WatchEventsResponse>('/api/watch-events'),
  item: (id: number) => request<FinanceItemDetail>(`/api/items/${id}`),
  sources: () => request<SourceStatus[]>('/api/sources'),
  translationStatus: () => request<TranslationStatus>('/api/translation/status'),
  translate: (itemIds: number[], maxNew = 12) => request<TranslationBatchResult>('/api/translate', {
    method: 'POST',
    body: JSON.stringify({ item_ids: itemIds, max_new: maxNew }),
  }),
  readerTranslation: (id: number) => request<ReaderTranslationResult>(`/api/items/${id}/reader-translation`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  save: (id: number, value: boolean) => request<{ ok: boolean }>(`/api/items/${id}/save`, {
    method: 'POST',
    body: JSON.stringify({ value }),
  }),
  read: (id: number) => request<{ ok: boolean }>(`/api/items/${id}/read`, {
    method: 'POST',
    body: JSON.stringify({ value: true }),
  }),
};
