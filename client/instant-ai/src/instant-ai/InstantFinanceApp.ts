import { instantApi } from './api';
import { FinancePanel } from './FinancePanel';
import type {
  AppStatus, FinanceItem, FinanceItemDetail, ReaderTranslationResult, SectionDefinition, SourceStatus,
  TranslationBatchResult, TranslationStatus,
} from './types';

const SECTIONS: SectionDefinition[] = [
  { id: 'global', title: '全球即时财经', subtitle: 'GLOBAL FINANCIAL WIRE', accent: '#ff9d35' },
  { id: 'wall-street', title: '华尔街与全球投行', subtitle: 'WALL STREET · REUTERS · BANK RESEARCH', topic: '华尔街', accent: '#42d6a4' },
  { id: 'china', title: '中国财经', subtitle: 'CHINA MARKETS · POLICY · FILINGS', topic: '中国财经', accent: '#ff5c65' },
  { id: 'asia', title: '亚洲市场', subtitle: 'JAPAN · KOREA · SINGAPORE · INDIA', topic: '亚洲市场', accent: '#56a8ff' },
  { id: 'gold', title: '黄金与贵金属', subtitle: 'GOLD · SILVER · CENTRAL BANK FLOWS', topic: '黄金', accent: '#f2c94c' },
  { id: 'zijin', title: '紫金矿业与全球矿业', subtitle: 'ZIJIN · MINES · M&A · PRODUCTION', topic: '紫金矿业', accent: '#d9913d' },
  { id: 'metals', title: '铜与有色金属', subtitle: 'COPPER · LITHIUM · RARE METALS', topic: '铜/有色', accent: '#d27d49' },
  { id: 'ai', title: 'AI、芯片与大型科技', subtitle: 'NVIDIA · GOOGLE · APPLE · SEMICONDUCTORS', topic: 'AI产业链', accent: '#a57bff' },
  { id: 'macro', title: '央行、利率与宏观', subtitle: 'FED · PBOC · ECB · BOJ · DATA', topic: '宏观政策', accent: '#4cc9f0' },
  { id: 'geopolitics', title: '战争、制裁与供应链', subtitle: 'MARKET IMPACT ONLY · NO LIVE VIDEO', topic: '战争/地缘', accent: '#ff6b6b' },
  { id: 'venture', title: '创业、融资与并购', subtitle: 'AI STARTUPS · VC · IPO · M&A', topic: '创业融资', accent: '#61d095' },
  { id: 'knowledge', title: '纳斯达克与财经知识', subtitle: 'INVESTOR EDUCATION · MARKET STRUCTURE', topic: '财经知识', accent: '#8aa4ff' },
];

const formatFullTime = (value: string | null): string => {
  if (!value) return '时间待确认';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short', hour12: false }).format(date);
};

export class InstantFinanceApp {
  private readonly root: HTMLElement;
  private readonly panels = new Map<string, FinancePanel>();
  private readonly sectionItems = new Map<string, FinanceItem[]>();
  private latestItems: FinanceItem[] = [];
  private instantHotItems: FinanceItem[] = [];
  private activeSectionId = 'global';
  private activeDetail: FinanceItemDetail | null = null;
  private translationEnabled = true;
  private translationInFlight = false;
  private translationStatus: TranslationStatus | null = null;
  private lastRefreshStartedAt = 0;

  constructor(root: HTMLElement) {
    this.root = root;
    try {
      this.translationEnabled = localStorage.getItem('instant-ai-translation-enabled') !== 'false';
    } catch {
      this.translationEnabled = true;
    }
  }

  async start(): Promise<void> {
    this.renderShell();
    this.createPanels();
    this.selectSection(this.activeSectionId, false);
    this.bindEvents();
    await this.refresh();
    window.setInterval(() => void this.refresh(false), 60_000);
  }

  private renderShell(): void {
    this.root.innerHTML = `
      <div class="terminal-shell">
        <header class="terminal-header">
          <div class="brand-block"><span class="brand-mark">即</span><div><strong>即时 AI</strong><small>全球财经情报终端</small></div></div>
          <div class="header-status"><span class="status-dot"></span><b id="healthText">连接资讯数据核心</b><span id="lastUpdate">--:--</span></div>
          <div class="header-actions">
            <label class="terminal-search"><span>⌕</span><input id="searchInput" autocomplete="off" placeholder="搜索公司、人物、商品或事件" /></label>
            <div class="header-tools" role="group" aria-label="客户端状态工具">
              <button type="button" class="translate-button" data-action="translate" title="把英文财经标题翻译成中文，并保留英文原题">汉化开启</button>
              <button type="button" data-action="sources" title="查看文字来源状态">来源</button>
              <div class="auto-collection" title="客户端启动后立即更新一轮，之后每 5 分钟自动采集全球财经文字来源">
                <span></span><b id="collectionMode">自动实时采集</b><small id="collectionCadence" class="visually-hidden">每 5 分钟持续更新</small>
              </div>
            </div>
          </div>
        </header>
        <section class="ticker-board" aria-label="即时热点">
          <div class="ticker ticker-highlight" title="融合最新发布、多来源关注度、重要度和新鲜度，交错去重展示">
            <b>即时热点</b><div class="ticker-window"><div id="tickerTrack" class="ticker-track"><span>正在汇总最新与重要财经事件…</span></div></div>
          </div>
        </section>
        <div class="terminal-body">
          <aside class="rail">
            <div class="rail-title">情报频道</div>
            <nav id="sectionNav"></nav>
            <div class="rail-title">全球市场中心</div>
            <div class="region-grid">
              <button data-query="美国 华尔街">纽约</button><button data-query="中国 A股 港股">中国</button>
              <button data-query="日本 韩国 新加坡 印度">亚洲</button><button data-query="欧洲 欧元 英国">欧洲</button>
              <button data-query="中东 战争 制裁 原油">中东</button><button data-query="非洲 矿业 黄金 铜">资源国</button>
            </div>
            <div class="translation-note"><b>标题汉化工具</b><small>中文译文在上 · 英文原题保留 · 译文安全缓存</small></div>
            <div class="local-only"><span></span><div><b>个人数据模式</b><small>无注册 · 无团队 · 无自动视频</small></div></div>
          </aside>
          <main class="workspace">
            <section class="pulse-board">
            <div><span>当前窗口</span><strong id="totalItems">0</strong></div>
              <div><span>未读消息</span><strong id="unreadItems">0</strong></div>
              <div><span>来源健康</span><strong id="sourceHealth">0/0</strong></div>
              <div><span>重要提醒</span><strong id="alertCount">0</strong></div>
              <div class="coverage"><span>覆盖范围</span><p>全球媒体 · 中国/亚洲 · 黄金矿业 · AI产业 · 宏观央行 · 地缘供应链</p></div>
            </section>
            <section id="searchResults" class="search-results hidden"></section>
            <section id="panelGrid" class="panel-grid" aria-live="polite" aria-label="当前频道内容"></section>
          </main>
        </div>
      </div>
      <nav class="mobile-dock" aria-label="手机版快捷频道">
        <button type="button" data-target="global" class="is-active"><span>即</span><b>最新</b></button>
        <button type="button" data-target="wall-street"><span>街</span><b>华尔街</b></button>
        <button type="button" data-target="china"><span>中</span><b>中国</b></button>
        <button type="button" data-target="gold"><span>金</span><b>黄金</b></button>
        <button type="button" data-target="ai"><span>AI</span><b>科技</b></button>
      </nav>
      <div id="overlay" class="overlay hidden"><article id="overlayCard" class="overlay-card"></article></div>
      <div id="toast" class="toast hidden"></div>`;

    const nav = this.required('#sectionNav');
    SECTIONS.forEach((section) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.target = section.id;
      button.classList.toggle('is-active', section.id === this.activeSectionId);
      if (section.id === this.activeSectionId) button.setAttribute('aria-current', 'page');
      const dot = document.createElement('span');
      dot.style.background = section.accent;
      button.append(dot, section.title);
      nav.append(button);
    });
  }

  private createPanels(): void {
    const grid = this.required('#panelGrid');
    SECTIONS.forEach((section) => {
      const panel = new FinancePanel(section);
      panel.setLoading();
      grid.append(panel.element);
      this.panels.set(section.id, panel);
    });
  }

  private bindEvents(): void {
    const refreshOnResume = (): void => {
      if (document.visibilityState === 'hidden' || Date.now() - this.lastRefreshStartedAt < 5_000) return;
      void this.refresh(false);
    };
    document.addEventListener('visibilitychange', refreshOnResume);
    window.addEventListener('pageshow', refreshOnResume);
    window.addEventListener('online', refreshOnResume);

    this.root.addEventListener('click', (event) => {
      const target = event.target as HTMLElement;
      const itemButton = target.closest<HTMLElement>('[data-item-id]');
      if (itemButton?.dataset.itemId) {
        void this.openItem(Number(itemButton.dataset.itemId));
        return;
      }
      const navButton = target.closest<HTMLElement>('[data-target]');
      if (navButton?.dataset.target) {
        this.selectSection(navButton.dataset.target);
        return;
      }
      const queryButton = target.closest<HTMLElement>('[data-query]');
      if (queryButton?.dataset.query) {
        const input = this.required<HTMLInputElement>('#searchInput');
        input.value = queryButton.dataset.query;
        void this.search(queryButton.dataset.query);
        return;
      }
      const action = target.closest<HTMLElement>('[data-action]')?.dataset.action;
      if (action === 'translate') void this.toggleTranslation();
      if (action === 'sources') void this.openSources();
      if (action === 'close-overlay') this.closeOverlay();
      if (action === 'save-item' && this.activeDetail) void this.toggleSave();
      if (action === 'reader-translation' && this.activeDetail) void this.openReaderTranslation();
      if (action === 'clear-search') this.clearSearch();
    });

    this.required<HTMLInputElement>('#searchInput').addEventListener('keydown', (event) => {
      const input = event.currentTarget as HTMLInputElement;
      if (event.key === 'Enter') void this.search(input.value.trim());
      if (event.key === 'Escape') this.clearSearch();
    });
    this.required('#overlay').addEventListener('click', (event) => {
      if (event.target === event.currentTarget) this.closeOverlay();
    });
  }

  private selectSection(sectionId: string, resetViewport = true): void {
    if (!this.panels.has(sectionId)) return;
    this.activeSectionId = sectionId;
    this.clearSearch();

    this.root.querySelectorAll<HTMLElement>('[data-target]').forEach((button) => {
      const active = button.dataset.target === sectionId;
      button.classList.toggle('is-active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });

    this.panels.forEach((panel, id) => {
      const active = id === sectionId;
      panel.element.hidden = !active;
      panel.element.classList.toggle('is-active', active);
    });

    if (resetViewport) window.scrollTo({ top: 0, behavior: 'auto' });
  }

  private async refresh(showErrors = true): Promise<void> {
    this.lastRefreshStartedAt = Date.now();
    try {
      const [status, latest, hot, translationStatus] = await Promise.all([
        instantApi.status(), instantApi.items('', '', 120), instantApi.hot(40), instantApi.translationStatus(),
      ]);
      this.translationStatus = translationStatus;
      this.latestItems = latest;
      this.instantHotItems = this.selectInstantHotItems(latest, hot);
      this.renderStatus(status);
      this.updateTranslationButton();
      await Promise.all(SECTIONS.map(async (section) => {
        const panel = this.panels.get(section.id);
        if (!panel) return;
        try {
          const items = section.id === 'global' ? latest : await instantApi.items(section.topic || '', '', 36);
          this.sectionItems.set(section.id, items);
        } catch (error) {
          panel.setError(error instanceof Error ? error.message : '栏目读取失败');
        }
      }));
      this.renderCurrentItems();
      if (this.translationEnabled) void this.translateVisible(false);
    } catch (error) {
      this.required('#healthText').textContent = '资讯数据核心未连接';
      if (showErrors) this.toast(error instanceof Error ? error.message : '刷新失败', true);
    }
  }

  private renderStatus(status: AppStatus): void {
    this.required('#totalItems').textContent = String(status.items.total || 0);
    this.required('#unreadItems').textContent = String(status.items.unread || 0);
    this.required('#sourceHealth').textContent = `${Math.max(0, (status.sources.enabled || 0) - (status.sources.errors || 0))}/${status.sources.enabled || 0}`;
    this.required('#alertCount').textContent = String(status.notifications.pending || 0);
    this.required('#healthText').textContent = status.collection.running ? '全球财经正在自动更新' : '自动实时采集运行中';
    this.required('#lastUpdate').textContent = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date());
    this.required('#collectionMode').textContent = status.collection.running ? '正在采集' : '自动实时采集';
    const minutes = Math.max(1, Math.round((status.collection.interval_seconds || 300) / 60));
    const cadence = `每 ${minutes} 分钟持续更新`;
    this.required('#collectionCadence').textContent = cadence;
    this.required('.auto-collection').setAttribute('title', `客户端启动后立即更新一轮，${cadence}全球财经文字来源`);
  }

  private renderTicker(items: FinanceItem[]): void {
    const track = this.required('#tickerTrack');
    track.replaceChildren();
    const headlines = items.slice(0, 16);
    const append = (item: FinanceItem) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.itemId = String(item.id);
      const score = document.createElement('b');
      score.textContent = item.source_count > 1 ? `${item.source_count}源` : String(item.importance_score);
      const headline = this.displayTitle(item);
      button.append(score, document.createTextNode(headline));
      const titleParts = ['即时热点：融合最新发布、多来源关注度、重要度和新鲜度'];
      if (headline !== item.title) titleParts.push(`英文原题：${item.title}`);
      if (titleParts.length > 0) button.title = titleParts.join('\n');
      track.append(button);
    };
    headlines.forEach(append);
    headlines.forEach(append);
    if (headlines.length === 0) {
      const empty = document.createElement('span');
      empty.textContent = '正在自动接收并筛选最新重要财经事件，请稍候。';
      track.append(empty);
    }
  }

  private selectInstantHotItems(latestItems: FinanceItem[], hotItems: FinanceItem[]): FinanceItem[] {
    const timestamp = (item: FinanceItem): number => {
      const value = new Date(item.published_at || item.first_seen_at).getTime();
      return Number.isNaN(value) ? 0 : value;
    };
    const newest = [...latestItems].sort((first, second) => timestamp(second) - timestamp(first)).slice(0, 12);
    const important = hotItems.slice(0, 12);
    const merged: FinanceItem[] = [];
    const seen = new Set<number>();
    for (let index = 0; index < Math.max(newest.length, important.length); index += 1) {
      [newest[index], important[index]].forEach((item) => {
        if (item && !seen.has(item.id)) {
          seen.add(item.id);
          merged.push(item);
        }
      });
    }
    return merged.slice(0, 16);
  }

  private async search(query: string): Promise<void> {
    if (!query) return this.clearSearch();
    const container = this.required('#searchResults');
    container.classList.remove('hidden');
    container.textContent = '正在搜索财经证据库…';
    try {
      const items = await instantApi.items('', query, 80);
      if (this.translationEnabled) {
        try {
          const candidates = items.filter((item) => this.shouldTranslate(item)).slice(0, 12);
          if (candidates.length > 0) {
            const result = await instantApi.translate(candidates.map((item) => item.id), 8);
            this.applyTranslations(items, result);
            this.translationStatus = result.status;
            this.updateTranslationButton();
          }
        } catch {
          // Search remains usable with original titles when the optional translator is unavailable.
        }
      }
      container.replaceChildren();
      const header = document.createElement('header');
      const title = document.createElement('h2');
      title.textContent = `搜索：${query}`;
      const count = document.createElement('span');
      count.textContent = `${items.length} 条`;
      const close = document.createElement('button');
      close.type = 'button';
      close.dataset.action = 'clear-search';
      close.textContent = '关闭';
      header.append(title, count, close);
      container.append(header);
      const list = document.createElement('div');
      list.className = 'search-list';
      items.forEach((item) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.itemId = String(item.id);
        const thumbnail = document.createElement('img');
        thumbnail.className = 'search-thumbnail';
        thumbnail.src = item.thumbnail_url;
        thumbnail.alt = '';
        thumbnail.loading = 'lazy';
        thumbnail.decoding = 'async';
        const copy = document.createElement('div');
        copy.className = 'search-result-copy';
        const meta = document.createElement('span');
        meta.textContent = `${item.importance_score} · ${item.sources?.[0] || item.event_type} · ${formatFullTime(item.published_at || item.first_seen_at)}`;
        const titleNode = document.createElement('b');
        const displayTitle = this.displayTitle(item);
        titleNode.textContent = displayTitle;
        copy.append(meta, titleNode);
        if (displayTitle !== item.title) {
          const original = document.createElement('small');
          original.className = 'search-original-title';
          original.textContent = `英文原题：${item.title}`;
          copy.append(original);
        }
        button.append(thumbnail, copy);
        list.append(button);
      });
      container.append(list);
      container.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
      container.textContent = error instanceof Error ? error.message : '搜索失败';
    }
  }

  private clearSearch(): void {
    this.required<HTMLInputElement>('#searchInput').value = '';
    const container = this.required('#searchResults');
    container.classList.add('hidden');
    container.replaceChildren();
  }

  private async openItem(id: number): Promise<void> {
    try {
      let item = await instantApi.item(id);
      if (this.translationEnabled && this.shouldTranslate(item)) {
        try {
          const result = await instantApi.translate([id], 1);
          this.translationStatus = result.status;
          item = await instantApi.item(id);
          this.updateTranslationButton();
        } catch {
          // The evidence view still opens with the source title if translation fails.
        }
      }
      this.activeDetail = item;
      void instantApi.read(id);
      const card = this.required('#overlayCard');
      card.replaceChildren();
      const close = document.createElement('button');
      close.className = 'overlay-close';
      close.type = 'button';
      close.dataset.action = 'close-overlay';
      close.textContent = '×';
      const kicker = document.createElement('div');
      kicker.className = 'detail-kicker';
      kicker.textContent = `${item.event_type} · 重要度 ${item.importance_score} · 可信级别 ${item.trust_level}/5`;
      const title = document.createElement('h1');
      const displayTitle = this.displayTitle(item);
      title.textContent = displayTitle;
      const originalTitle = document.createElement('p');
      originalTitle.className = 'detail-original-title';
      originalTitle.textContent = displayTitle !== item.title ? `英文原题：${item.title}` : '';
      originalTitle.classList.toggle('hidden', !originalTitle.textContent);
      const meta = document.createElement('p');
      meta.className = 'detail-meta';
      meta.textContent = `${formatFullTime(item.published_at || item.first_seen_at)} · ${(item.sources || []).join('、') || `${item.source_count} 个来源`}`;
      const detailHeader = document.createElement('div');
      detailHeader.className = 'detail-header';
      const detailThumbnail = document.createElement('img');
      detailThumbnail.className = 'detail-thumbnail';
      detailThumbnail.src = item.thumbnail_url;
      detailThumbnail.alt = '';
      detailThumbnail.decoding = 'async';
      const detailCopy = document.createElement('div');
      detailCopy.className = 'detail-copy';
      detailCopy.append(kicker, title, originalTitle, meta);
      detailHeader.append(detailThumbnail, detailCopy);
      const summary = document.createElement('p');
      summary.className = 'detail-summary';
      summary.textContent = item.summary || '来源只提供了标题；临时抓取证据会随热点窗口自动清理。';
      const actions = document.createElement('div');
      actions.className = 'detail-actions';
      const translateReader = document.createElement('button');
      translateReader.type = 'button';
      translateReader.dataset.action = 'reader-translation';
      translateReader.textContent = '中文摘要（备用）';
      translateReader.title = '只翻译资讯源已经提供的摘要，不抓取新闻全文';
      const original = document.createElement('a');
      original.href = this.safeUrl(item.url);
      original.target = '_blank';
      original.rel = 'noopener noreferrer';
      original.referrerPolicy = 'no-referrer';
      original.textContent = '浏览器翻译原文';
      original.title = '在默认浏览器的新标签页打开，使用 Chrome 的网页翻译';
      const save = document.createElement('button');
      save.type = 'button';
      save.dataset.action = 'save-item';
      save.textContent = item.is_saved ? '取消置顶' : '临时置顶';
      actions.append(original, translateReader, save);
      const browserTranslationTip = document.createElement('p');
      browserTranslationTip.className = 'browser-translation-tip';
      browserTranslationTip.textContent = '原文将在默认浏览器的新标签页打开；Chrome 可开启“始终翻译英语”。';
      const readerTranslation = document.createElement('section');
      readerTranslation.id = 'readerTranslation';
      readerTranslation.className = 'reader-translation hidden';
      const evidenceTitle = document.createElement('h2');
      evidenceTitle.textContent = `当前证据（${item.evidence.length}）`;
      const evidence = document.createElement('div');
      evidence.className = 'evidence-list';
      item.evidence.forEach((entry) => {
        const row = document.createElement('div');
        const name = document.createElement('b');
        name.textContent = entry.source_name;
        const info = document.createElement('span');
        info.textContent = `${formatFullTime(entry.fetched_at)} · SHA-256 ${entry.content_hash.slice(0, 16)}…`;
        row.append(name, info);
        evidence.append(row);
      });
      card.append(close, detailHeader, summary, actions, browserTranslationTip, readerTranslation, evidenceTitle, evidence);
      this.required('#overlay').classList.remove('hidden');
    } catch (error) {
      this.toast(error instanceof Error ? error.message : '详情读取失败', true);
    }
  }

  private async openReaderTranslation(): Promise<void> {
    if (!this.activeDetail) return;
    const itemId = this.activeDetail.id;
    const button = this.required<HTMLButtonElement>('[data-action="reader-translation"]');
    const container = this.required<HTMLElement>('#readerTranslation');
    button.disabled = true;
    button.textContent = '翻译中…';
    container.classList.remove('hidden');
    container.replaceChildren();
    const loading = document.createElement('p');
    loading.className = 'reader-loading';
    loading.textContent = '正在翻译资讯源已经提供的摘要；即时 AI 不抓取新闻全文。';
    container.append(loading);
    try {
      const result = await instantApi.readerTranslation(itemId);
      if (result.status) {
        this.translationStatus = result.status;
        this.updateTranslationButton();
      }
      if (!result.ok || !result.translated_text) {
        const message = result.quota_exhausted
          ? '今日免费摘要翻译额度已用完；仍可用浏览器打开并翻译原文。'
          : result.error === 'no_public_text'
            ? '该资讯源没有提供摘要，请使用“浏览器翻译原文”。'
            : '中文摘要暂时不可用，请使用“浏览器翻译原文”。';
        loading.textContent = message;
        loading.classList.add('error');
        button.textContent = '重试中文摘要';
        return;
      }
      this.renderReaderTranslation(container, result);
      button.textContent = result.cached ? '已显示中文摘要' : '中文摘要已生成';
    } catch (error) {
      loading.textContent = error instanceof Error ? error.message : '中文摘要生成失败';
      loading.classList.add('error');
      button.textContent = '重试中文摘要';
    } finally {
      button.disabled = false;
    }
  }

  private renderReaderTranslation(container: HTMLElement, result: ReaderTranslationResult): void {
    container.replaceChildren();
    const heading = document.createElement('div');
    heading.className = 'reader-heading';
    const title = document.createElement('h2');
    title.textContent = '中文摘要（备用）';
    const source = document.createElement('span');
    source.textContent = `来源摘要 · ${result.cached ? '短期缓存' : '刚刚生成'}`;
    heading.append(title, source);

    const chinese = document.createElement('div');
    chinese.className = 'reader-chinese';
    (result.translated_text || '').split(/\n{2,}/u).filter(Boolean).forEach((paragraph) => {
      const line = document.createElement('p');
      line.textContent = paragraph;
      chinese.append(line);
    });

    const notes: string[] = [];
    if (result.translation_partial) notes.push('受免费翻译额度限制，本次为部分译文');
    notes.push('即时 AI 不抓取新闻全文；完整内容请使用浏览器翻译原文');
    if (notes.length) {
      const note = document.createElement('p');
      note.className = 'reader-note';
      note.textContent = notes.join('；') + '。';
      container.append(heading, chinese, note);
    } else {
      container.append(heading, chinese);
    }

    if (result.original_excerpt) {
      const original = document.createElement('details');
      original.className = 'reader-original';
      const summary = document.createElement('summary');
      summary.textContent = '查看英文摘要';
      const body = document.createElement('div');
      result.original_excerpt.split(/\n{2,}/u).filter(Boolean).forEach((paragraph) => {
        const line = document.createElement('p');
        line.textContent = paragraph;
        body.append(line);
      });
      original.append(summary, body);
      container.append(original);
    }
  }

  private async openSources(): Promise<void> {
    try {
      const sources = await instantApi.sources();
      const card = this.required('#overlayCard');
      card.replaceChildren();
      const close = document.createElement('button');
      close.className = 'overlay-close';
      close.type = 'button';
      close.dataset.action = 'close-overlay';
      close.textContent = '×';
      const title = document.createElement('h1');
      title.textContent = `文字来源状态（${sources.length}）`;
      const note = document.createElement('p');
      note.className = 'detail-summary';
      note.textContent = '证据只在当前热点窗口临时保留。付费媒体只使用允许公开访问的标题、摘要或授权接口，不绕过付费墙。';
      const list = document.createElement('div');
      list.className = 'source-list';
      sources.forEach((source) => list.append(this.sourceRow(source)));
      card.append(close, title, note, list);
      this.required('#overlay').classList.remove('hidden');
    } catch (error) {
      this.toast(error instanceof Error ? error.message : '来源状态读取失败', true);
    }
  }

  private sourceRow(source: SourceStatus): HTMLElement {
    const row = document.createElement('div');
    row.className = `source-row ${source.last_error ? 'has-error' : ''}`;
    const main = document.createElement('div');
    const name = document.createElement('b');
    name.textContent = source.name;
    const topics = document.createElement('span');
    topics.textContent = source.topic_hints.join(' · ') || '综合来源';
    main.append(name, topics);
    const state = document.createElement('div');
    state.className = 'source-state';
    state.textContent = source.last_error ? '异常' : source.last_success_at ? '正常' : '待运行';
    const info = document.createElement('small');
    info.textContent = `可信 ${source.trust_level}/5 · 最近 ${source.last_item_count || 0} 条`;
    state.append(info);
    row.append(main, state);
    return row;
  }

  private async toggleSave(): Promise<void> {
    if (!this.activeDetail) return;
    const next = !this.activeDetail.is_saved;
    await instantApi.save(this.activeDetail.id, next);
    this.activeDetail.is_saved = next;
    const button = this.required<HTMLButtonElement>('[data-action="save-item"]');
    button.textContent = next ? '取消置顶' : '临时置顶';
    this.toast(next ? '已在当前窗口临时置顶，到期仍会自动清理' : '已取消临时置顶');
  }

  private renderCurrentItems(): void {
    this.renderTicker(this.instantHotItems);
    SECTIONS.forEach((section) => {
      const panel = this.panels.get(section.id);
      const items = this.sectionItems.get(section.id);
      if (panel && items) panel.render(items, this.translationEnabled);
    });
  }

  private async toggleTranslation(): Promise<void> {
    this.translationEnabled = !this.translationEnabled;
    try {
      localStorage.setItem('instant-ai-translation-enabled', String(this.translationEnabled));
    } catch {
      // The in-memory setting remains usable if browser storage is unavailable.
    }
    this.updateTranslationButton();
    this.renderCurrentItems();
    if (this.translationEnabled) {
      this.toast('标题汉化已开启：中文译文在上，英文原题保留在下。');
      await this.translateVisible(true);
    } else {
      this.toast('标题汉化已关闭，当前显示英文原题。');
    }
  }

  private async translateVisible(showToast: boolean): Promise<void> {
    if (!this.translationEnabled || this.translationInFlight) return;
    const priorityItems = [
      ...this.latestItems.slice(0, 16),
      ...this.instantHotItems.slice(0, 16),
      ...SECTIONS.flatMap((section) => (this.sectionItems.get(section.id) || []).slice(0, 2)),
    ];
    const unique = Array.from(new Map(priorityItems.map((item) => [item.id, item])).values());
    const candidates = unique.filter((item) => this.shouldTranslate(item)).slice(0, 40);
    if (candidates.length === 0) {
      if (showToast) this.toast('当前显示的英文标题已经完成汉化。');
      return;
    }

    this.translationInFlight = true;
    this.updateTranslationButton();
    try {
      const result = await instantApi.translate(candidates.map((item) => item.id), 12);
      this.applyTranslations(this.latestItems, result);
      this.applyTranslations(this.instantHotItems, result);
      this.sectionItems.forEach((items) => this.applyTranslations(items, result));
      this.translationStatus = result.status;
      this.renderCurrentItems();
      if (showToast) {
        if (result.translated_count > 0) {
          this.toast(`已新增 ${result.translated_count} 条中文标题，译文已缓存到本机。`);
        } else if (result.quota_exhausted) {
          this.toast('今日免费翻译额度已用完；已有译文仍可继续使用。', true);
        } else if (result.errors.length > 0) {
          this.toast('翻译通道暂时不可用，已保留英文原题。', true);
        } else {
          this.toast('当前标题已有译文缓存。');
        }
      }
    } catch (error) {
      if (showToast) this.toast(error instanceof Error ? error.message : '标题翻译失败', true);
    } finally {
      this.translationInFlight = false;
      this.updateTranslationButton();
    }
  }

  private applyTranslations(items: FinanceItem[], result: TranslationBatchResult): void {
    items.forEach((item) => {
      const translated = result.translations[String(item.id)];
      if (translated) {
        item.translated_title = translated;
        item.translation_provider = result.providers[String(item.id)] || result.status.provider;
      }
    });
  }

  private shouldTranslate(item: FinanceItem): boolean {
    if (item.translated_title?.trim()) return false;
    const hasChinese = /[\u3400-\u9fff]/u.test(item.title);
    const latinLetters = (item.title.match(/[A-Za-z]/g) || []).length;
    return !hasChinese && latinLetters >= 6;
  }

  private displayTitle(item: FinanceItem): string {
    if (!this.translationEnabled) return item.title;
    return item.translated_title?.trim() || item.title;
  }

  private updateTranslationButton(): void {
    const button = this.root.querySelector<HTMLButtonElement>('[data-action="translate"]');
    if (!button) return;
    button.classList.toggle('is-active', this.translationEnabled);
    button.disabled = this.translationInFlight;
    button.textContent = this.translationInFlight ? '汉化中…' : this.translationEnabled ? '汉化开启' : '汉化关闭';
    const remaining = this.translationStatus?.remaining_characters_today;
    const provider = this.translationStatus?.provider_label || '标题翻译';
    button.title = remaining == null
      ? `${provider}；译文安全缓存，英文原题始终保留。`
      : `${provider}；今日免费安全余量约 ${remaining} 字符；译文安全缓存。`;
  }

  private closeOverlay(): void {
    this.required('#overlay').classList.add('hidden');
    this.activeDetail = null;
  }

  private toast(message: string, error = false): void {
    const toast = this.required('#toast');
    toast.textContent = message;
    toast.className = `toast ${error ? 'error' : ''}`.trim();
    window.setTimeout(() => toast.classList.add('hidden'), 4500);
  }

  private safeUrl(value: string): string {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '#';
    } catch {
      return '#';
    }
  }

  private required<T extends Element = HTMLElement>(selector: string): T {
    const element = this.root.querySelector<T>(selector) || document.querySelector<T>(selector);
    if (!element) throw new Error(`Missing interface element: ${selector}`);
    return element;
  }
}
