const state = { view: 'focus', topic: '', query: '', items: [], status: null };
const $ = (selector) => document.querySelector(selector);
const content = $('#content');
const notice = $('#notice');

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method === 'POST') {
    headers['Content-Type'] = 'application/json';
    headers['X-Instant-AI'] = '1';
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) throw new Error(`请求失败 (${response.status})`);
  return response.json();
}

function escapeHtml(value = '') {
  const node = document.createElement('div');
  node.textContent = value;
  return node.innerHTML;
}

function safeExternalUrl(value = '') {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? escapeHtml(url.href) : '#';
  } catch { return '#'; }
}

function formatTime(value) {
  if (!value) return '时间未知';
  try { return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)); }
  catch { return value; }
}

function showNotice(message, error = false) {
  notice.textContent = message;
  notice.className = `notice${error ? ' error' : ''}`;
  setTimeout(() => notice.classList.add('hidden'), 8000);
}

function renderStats(status) {
  const items = status.items || {};
  const sources = status.sources || {};
  const last = status.last_run;
  $('#stats').innerHTML = `
    <div class="stat-card"><span>情报总量</span><strong>${items.total || 0}</strong><small>条</small></div>
    <div class="stat-card"><span>尚未阅读</span><strong>${items.unread || 0}</strong><small>条</small></div>
    <div class="stat-card"><span>已收藏</span><strong>${items.saved || 0}</strong><small>条</small></div>
    <div class="stat-card"><span>来源健康</span><strong>${(sources.enabled || 0) - (sources.errors || 0)}/${sources.enabled || 0}</strong><small>${last ? escapeHtml(last.status) : '待采集'}</small></div>`;
  $('#alert-count').textContent = status.notifications?.pending || 0;
}

function itemCard(item) {
  const scoreClass = item.importance_score >= 85 ? 'hot' : (item.importance_score >= 70 ? 'high' : '');
  const summary = item.summary || '已保存来源标题和原始证据，打开详情可查看来源。';
  return `<article class="item-card" data-id="${item.id}">
    <div class="score ${scoreClass}">${item.importance_score}</div>
    <div>
      <h2>${escapeHtml(item.title)}</h2>
      <p>${escapeHtml(summary)}</p>
      <div class="tags">
        <span class="tag event">${escapeHtml(item.event_type)}</span>
        ${(item.topics || []).map(topic => `<span class="tag">${escapeHtml(topic)}</span>`).join('')}
        <span>${formatTime(item.published_at || item.first_seen_at)}</span>
        <span>${item.source_count} 个来源</span>
      </div>
    </div>
    <button class="save-button ${item.is_saved ? 'saved' : ''}" data-save="${item.id}" title="收藏">★</button>
  </article>`;
}

async function loadItems() {
  const params = new URLSearchParams({ limit: '120' });
  if (state.topic) params.set('topic', state.topic);
  if (state.query) params.set('q', state.query);
  if (state.view === 'saved') params.set('saved', '1');
  const items = await api(`/api/items?${params}`);
  state.items = state.view === 'focus' ? items.filter(item => item.importance_score >= 60).slice(0, 40) : items;
  content.innerHTML = state.items.length
    ? state.items.map(itemCard).join('')
    : `<div class="empty"><strong>这里暂时没有情报</strong>${state.status?.collection?.running ? '首次采集正在进行，请稍候。' : '点击“立即采集”从已启用的官方来源获取数据。'}</div>`;
}

async function loadSources() {
  const sources = await api('/api/sources');
  content.innerHTML = sources.map(source => `<article class="source-card">
    <div><h3>${escapeHtml(source.name)}</h3><p>${escapeHtml(source.url)}</p><p>可信级别 ${source.trust_level}/5 · 最近 ${source.last_item_count || 0} 条 · ${source.last_success_at ? formatTime(source.last_success_at) : '尚未成功采集'}</p>${source.last_error ? `<p class="source-status error">${escapeHtml(source.last_error)}</p>` : ''}</div>
    <div class="source-status ${source.last_error ? 'error' : ''}">${source.last_error ? '异常' : (source.last_success_at ? '正常' : '待运行')}<br><button class="toggle ${source.enabled ? 'on' : ''}" data-source="${source.id}" data-enabled="${source.enabled}"></button></div>
  </article>`).join('');
}

async function loadAlerts() {
  const alerts = await api('/api/notifications');
  content.innerHTML = alerts.length ? alerts.map(alert => `<article class="source-card alert-card" data-alert-item="${alert.item_id}">
    <div><div class="tags"><span class="tag event">${escapeHtml(alert.event_type)}</span>${alert.topics.map(topic => `<span class="tag">${escapeHtml(topic)}</span>`).join('')}</div><h3>${escapeHtml(alert.title)}</h3><p>${escapeHtml(alert.reason.reason)}</p><p>重要度 ${alert.importance_score} · ${formatTime(alert.published_at || alert.first_seen_at)}</p></div>
    <button class="button ghost" data-dismiss-alert="${alert.id}">知道了</button>
  </article>`).join('') : '<div class="empty"><strong>没有待处理的重要提醒</strong>只有高可信来源与高重要度规则同时命中时才会进入这里。</div>';
}

function loadStorage() {
  const status = state.status;
  content.innerHTML = `<article class="storage-card">
    <h3>正式业务文件库</h3><p>${escapeHtml(status.library_path)}</p>
    <h3>SQLite 主数据库</h3><p>${escapeHtml(status.database_path)}</p>
    <h3>最近备份</h3><p>${escapeHtml(status.latest_backup || '尚未创建')}</p>
    <div class="detail-actions"><button class="button primary" id="backup-button">立即备份数据库</button><button class="button ghost" id="restore-drill-button">验证备份可恢复</button></div>
    <h3>AI 后处理</h3><p id="ai-status">读取状态中…</p>
    <h3>目录说明</h3><p>raw：原始抓取证据　 evidence：证据清单　 database：正式数据库　 exports：导出　 backups：备份　 cache：可删除缓存　 logs：脱敏日志</p>
    <p>本客户端只监听 127.0.0.1，不建立账户，正式数据不上传 GitHub。</p>
  </article>`;
  $('#backup-button').onclick = async () => { const result = await api('/api/backup', { method: 'POST', body: '{}' }); showNotice(`数据库备份已保存：${result.path}`); await refresh(); };
  $('#restore-drill-button').onclick = async () => { const result = await api('/api/restore-drill', { method: 'POST', body: '{}' }); showNotice(`恢复演练通过：${result.result.item_count} 条，完整性 ${result.result.integrity_check}`); await refresh(); };
  api('/api/ai/status').then(ai => { $('#ai-status').textContent = ai.message; }).catch(() => {});
}

async function refresh() {
  state.status = await api('/api/status');
  renderStats(state.status);
  const button = $('#collect-button');
  button.disabled = Boolean(state.status.collection.running);
  button.innerHTML = state.status.collection.running ? '<span>↻</span> 采集中…' : '<span>↻</span> 立即采集';
  if (state.view === 'sources') await loadSources();
  else if (state.view === 'alerts') await loadAlerts();
  else if (state.view === 'storage') loadStorage();
  else await loadItems();
}

async function openDetail(id) {
  const item = await api(`/api/items/${id}`);
  await api(`/api/items/${id}/read`, { method: 'POST', body: JSON.stringify({ value: true }) });
  const panel = $('#detail-panel');
  panel.innerHTML = `<button class="detail-close" id="detail-close">×</button>
    <div class="tags"><span class="tag event">${escapeHtml(item.event_type)}</span>${item.topics.map(topic => `<span class="tag">${escapeHtml(topic)}</span>`).join('')}</div>
    <h2>${escapeHtml(item.title)}</h2>
    <div class="meta"><span>重要度 ${item.importance_score}</span><span>${formatTime(item.published_at || item.first_seen_at)}</span><span>${item.source_count} 个来源</span></div>
    <p class="detail-summary">${escapeHtml(item.summary || '该来源目前只提供标题；原始页面和抓取证据已保存。')}</p>
    <div class="detail-actions"><a class="button primary" href="${safeExternalUrl(item.url)}" target="_blank" rel="noreferrer">打开原文</a><button class="button ghost" id="detail-save">${item.is_saved ? '取消收藏' : '收藏'}</button><button class="button ghost" id="detail-analyze">准备 AI 证据包</button></div>
    <p class="ai-state">${item.ai_job ? `AI 任务：${escapeHtml(item.ai_job.status)}（不会冒充已生成摘要）` : '尚未创建 AI 任务；现有评分来自确定性规则。'}</p>
    <h3 class="section-title">证据链</h3>
    ${(item.evidence || []).map(e => `<div class="evidence-card"><strong>${escapeHtml(e.source_name)}</strong><p>抓取：${formatTime(e.fetched_at)} · SHA-256：${escapeHtml(e.content_hash)}</p><a href="/api/evidence/${e.id}/raw" target="_blank">查看本地原始证据</a></div>`).join('')}`;
  $('#detail-backdrop').classList.remove('hidden');
  $('#detail-close').onclick = closeDetail;
  $('#detail-save').onclick = async () => { await saveItem(item.id, !item.is_saved); closeDetail(); await refresh(); };
  $('#detail-analyze').onclick = async () => { const result = await api(`/api/items/${item.id}/analyze`, { method: 'POST', body: '{}' }); showNotice(result.status === 'waiting_for_provider' ? '证据包已保存；配置真实模型后再执行，不影响当前阅读。' : '证据包已保存；联网执行器启用后再运行。'); closeDetail(); };
}

function closeDetail() { $('#detail-backdrop').classList.add('hidden'); }
async function saveItem(id, value) { await api(`/api/items/${id}/save`, { method: 'POST', body: JSON.stringify({ value }) }); }

document.addEventListener('click', async event => {
  const save = event.target.closest('[data-save]');
  if (save) { event.stopPropagation(); const item = state.items.find(x => x.id === Number(save.dataset.save)); await saveItem(item.id, !item.is_saved); await refresh(); return; }
  const card = event.target.closest('.item-card'); if (card) { await openDetail(card.dataset.id); return; }
  const source = event.target.closest('[data-source]'); if (source) { await api(`/api/sources/${source.dataset.source}/toggle`, { method: 'POST', body: JSON.stringify({ enabled: source.dataset.enabled !== 'true' }) }); await refresh(); }
  const dismiss = event.target.closest('[data-dismiss-alert]'); if (dismiss) { event.stopPropagation(); await api(`/api/notifications/${dismiss.dataset.dismissAlert}/dismiss`, { method: 'POST', body: '{}' }); await refresh(); return; }
  const alert = event.target.closest('[data-alert-item]'); if (alert) { await openDetail(alert.dataset.alertItem); }
});

$('#nav').addEventListener('click', async event => {
  const button = event.target.closest('.nav-item'); if (!button) return;
  document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active')); button.classList.add('active');
  state.topic = button.dataset.topic || ''; state.view = button.dataset.view || 'topic'; state.query = ''; $('#search-input').value = '';
  const titles = { focus: ['今日重点', '只显示最值得关注的新变化'], all: ['全部情报', '按重要度和时间查看所有证据'], saved: ['我的收藏', '本机保存的重要条目'], sources: ['来源状态', '官方来源、最近运行和失败原因'], alerts: ['重要提醒', '只有高可信与高重要度同时命中的低噪声提醒'], storage: ['数据位置', '所有正式数据都只保存在 H 盘'] };
  const selected = titles[state.view] || [state.topic, `专题：${state.topic}`]; $('#view-title').textContent = selected[0]; $('#view-subtitle').textContent = selected[1];
  await refresh();
});

let searchTimer;
$('#search-input').addEventListener('input', event => { clearTimeout(searchTimer); searchTimer = setTimeout(async () => { state.query = event.target.value.trim(); state.view = 'all'; state.topic = ''; $('#view-title').textContent = state.query ? `搜索：${state.query}` : '全部情报'; await refresh(); }, 300); });
$('#collect-button').addEventListener('click', async () => { await api('/api/collect', { method: 'POST', body: '{}' }); showNotice('采集任务已经开始，数据将直接写入 H 盘。'); await refresh(); });
$('#export-button').addEventListener('click', async () => { const result = await api('/api/export'); showNotice(`已导出到：${result.path}`); });
$('#detail-backdrop').addEventListener('click', event => { if (event.target.id === 'detail-backdrop') closeDetail(); });

refresh().catch(error => showNotice(error.message, true));
setInterval(() => { if (state.status?.collection?.running) refresh().catch(() => {}); }, 2500);
