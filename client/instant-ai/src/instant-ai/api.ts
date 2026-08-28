import type {
  AppStatus, FinanceItem, FinanceItemDetail, SourceStatus, TranslationBatchResult, TranslationStatus,
} from './types';

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method === 'POST') {
    headers.set('Content-Type', 'application/json');
    headers.set('X-Instant-AI', '1');
  }
  const response = await fetch(path, { ...init, headers, cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`资讯接口请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const instantApi = {
  status: () => request<AppStatus>('/api/status'),
  items: (topic = '', query = '', limit = 40) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (topic) params.set('topic', topic);
    if (query) params.set('q', query);
    return request<FinanceItem[]>(`/api/items?${params.toString()}`);
  },
  hot: (limit = 40) => request<FinanceItem[]>(`/api/hot?limit=${limit}`),
  item: (id: number) => request<FinanceItemDetail>(`/api/items/${id}`),
  sources: () => request<SourceStatus[]>('/api/sources'),
  translationStatus: () => request<TranslationStatus>('/api/translation/status'),
  translate: (itemIds: number[], maxNew = 12) => request<TranslationBatchResult>('/api/translate', {
    method: 'POST',
    body: JSON.stringify({ item_ids: itemIds, max_new: maxNew }),
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
