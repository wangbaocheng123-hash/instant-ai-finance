import type { WatchEvent, WatchEventsResponse } from './types';
import type { HeadlineTarget } from './VisibleTitleTranslation';
import { sourceReadingLinks } from './BrowserReading';

type TitleRecord = { title: string; translated_title?: string | null; translation_provider?: string | null };

const formatEventDate = (date: string, time: string): { month: string; day: string; full: string } => {
  const [year, month, day] = date.split('-').map(Number);
  if (!year || !month || !day) return { month: '--', day: '--', full: date };
  return {
    month: `${month}月`,
    day: String(day),
    full: `${year}年${month}月${day}日${time ? ` ${time}` : ''}`,
  };
};

const scopeLabel = (scope: WatchEvent['scope']): string => scope === 'home' ? '罗盘首页' : '紫金时间线';

export class WatchEventsPanel {
  public readonly element: HTMLElement;
  private readonly body: HTMLElement;
  private readonly count: HTMLElement;

  private readonly titles = new Map<string, TitleRecord>();
  private useChinese = true;

  constructor(private readonly completed: (title: string) => string | undefined = () => undefined) {
    this.element = document.createElement('section');
    this.element.className = 'finance-panel watch-events-panel';
    this.element.dataset.panel = 'watch-events';
    this.element.style.setProperty('--panel-accent', '#e44758');

    const header = document.createElement('header');
    header.className = 'panel-header';
    const heading = document.createElement('div');
    heading.className = 'panel-heading';
    const title = document.createElement('h2');
    title.textContent = '重点事件关注';
    const subtitle = document.createElement('span');
    subtitle.textContent = 'TIME COMPASS · INSTANT AI MONITOR';
    heading.append(title, subtitle);
    this.count = document.createElement('strong');
    this.count.className = 'panel-count';
    this.count.textContent = '0';
    header.append(heading, this.count);

    this.body = document.createElement('div');
    this.body.className = 'panel-body watch-events-body';
    this.element.append(header, this.body);
  }

  setLoading(): void {
    this.body.replaceChildren(this.message('正在同步罗盘重点事件…'));
  }

  setError(message: string): void {
    this.body.replaceChildren(this.message(message, 'error'));
  }

  render(response: WatchEventsResponse, useChinese = true): void {
    this.useChinese = useChinese;
    this.titles.clear();
    const policyCount = response.monetaryPolicy?.signals.length || 0;
    this.count.textContent = String(response.counts.total + policyCount);
    const summary = document.createElement('div');
    summary.className = 'watch-summary';
    summary.append(
      this.metric('全部', response.counts.total + policyCount),
      this.metric('首页', response.counts.home + policyCount),
      this.metric('紫金', response.counts.zijin),
      this.metric('官方正常', response.counts.official_reachable),
    );
    const sync = document.createElement('p');
    sync.className = response.sync?.last_error ? 'watch-sync has-error' : 'watch-sync';
    sync.textContent = response.sync?.last_error
      ? '罗盘本轮暂未连通，继续使用上次同步列表监测。'
      : `已配置 ${response.counts.configured} 个事件的官方渠道；即时AI每 5 分钟检查到期渠道，已发现 ${response.counts.signals_detected} 个官方变化、送达罗盘 ${response.counts.signals_delivered} 个${response.sync?.last_success_at ? ` · 最近同步 ${this.formatClock(response.sync.last_success_at)}` : ''}`;

    const list = document.createElement('div');
    list.className = 'watch-event-list';
    const discoveredKeys = new Set(response.discovery?.events.map((event) => event.key) || []);
    response.events.filter((event) => !Array.from(discoveredKeys).some((key) => event.event_key.endsWith(`instant-ai-future:${key}`)))
      .forEach((event) => list.append(this.eventCard(event)));
    if (response.events.length === 0) list.append(this.message('罗盘尚未同步重点事件，连接后会自动出现。'));
    const discovery = this.discoveryPanel(response.discovery);
    const policy = this.policyPanel(response.monetaryPolicy);
    const reading = this.message('中文阅读通过 Google 翻译打开；iPhone 使用 Chrome。原站不支持时，可选 Chrome 原文并在菜单中点“翻译”。');
    reading.className = 'watch-sync';
    this.body.replaceChildren(...[policy, reading, discovery, summary, sync, list].filter((node): node is HTMLElement => node !== null));
  }

  private policyPanel(policy: WatchEventsResponse['monetaryPolicy']): HTMLElement | null {
    if (!policy) return null;
    const section = document.createElement('section');
    section.className = 'official-calendar-discovery monetary-policy-signals';
    const heading = document.createElement('h3');
    heading.textContent = `加息 / 降息 / 美联储政策 · ${policy.signals.length} 条`;
    section.append(heading, this.message('正式决议、官员表态、机构预测和市场预期分别核验；输送成功不等于政策已确认。'));
    if (policy.monitoring) {
      const active = policy.monitoring.sources.filter(source => source.state !== 'disabled');
      const unhealthy = policy.monitoring.sources.filter(source => source.state !== 'ok');
      const health = this.message(active.length === 0 ? '利率政策来源未启用，请查看采集状态。'
        : unhealthy.length ? `${unhealthy.length} 个重点政策来源暂停、待更新或读取异常；保留已核实线索，不代表近期没有政策变化。`
          : '已启用的重点政策来源近期读取正常；默认每5分钟检查，官方来源优先。', unhealthy.length || !active.length ? 'error' : '');
      section.append(health);
      for (const source of unhealthy) {
        const line = document.createElement('p'); line.className = 'watch-sync has-error';
        line.textContent = `${source.name}：${source.state === 'stale' ? '更新已延迟' : source.state === 'error' ? '最近读取失败' : source.state === 'disabled' ? '主人已暂停采集' : '等待首次读取'}${source.lastSuccessAt ? ` · 最近成功 ${this.formatClock(source.lastSuccessAt)}` : ''}`;
        section.append(line);
      }
    }
    for (const signal of policy.signals) {
      const card = document.createElement('article');
      card.className = 'watch-event-content watch-official';
      const title = this.headline(`watch:policy:${signal.key}`, signal);
      const kind = document.createElement('p');
      kind.textContent = `${signal.bankName} · ${signal.kindLabel}${signal.speaker ? ` · ${signal.speaker}` : ''}`;
      const origin = document.createElement('p');
      origin.textContent = `${signal.firstObservedSource.sourceRole === 'official-original' ? '官方原始渠道' : '已读报道渠道（首发待核验）'}：${signal.firstObservedSource.publisher}`;
      const time = document.createElement('p');
      const format = (value: string): string => value && !Number.isNaN(Date.parse(value))
        ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '待核实';
      time.textContent = `来源标注时间：${format(signal.publishedAt)}；系统发现：${format(signal.detectedAt)}（北京时间，原始首发时间另行核验）`;
      const pipeline = document.createElement('p');
      pipeline.className = signal.status === 'failed' ? 'watch-sync has-error' : 'watch-sync';
      pipeline.textContent = signal.pipelineStatus;
      card.append(title, kind, origin, time, pipeline);
      try {
        const url = new URL(signal.firstObservedSource.url);
        if (url.protocol === 'https:' && !url.username && !url.password) {
          const links = sourceReadingLinks(url.href, '报道来源');
          if (links) card.append(links);
        }
      } catch { /* Keep the clue visible while its source is being reviewed. */ }
      section.append(card);
    }
    if (!policy.signals.length) section.append(this.message('尚无新锁定的利率政策线索，已接入来源持续监测。'));
    section.append(this.message(policy.coverage));
    return section;
  }

  private discoveryPanel(discovery: WatchEventsResponse['discovery']): HTMLElement | null {
    if (!discovery) return null;
    const section = document.createElement('section');
    section.className = 'official-calendar-discovery';
    const heading = document.createElement('h3');
    heading.textContent = `官方会议预告 · ${discovery.events.length} 条`;
    const unhealthy = discovery.sources.filter((source) => source.state !== 'ok' && source.state !== 'disabled');
    const status = this.message(!discovery.enabled ? '已暂停官方会议日程采集。'
      : unhealthy.length ? `有 ${unhealthy.length} 个官方日程来源暂未完成更新，以下保留上次核实记录；请留意官方最新安排。`
        : '官方日程持续核对；已确认时点按北京时间显示，待定时间单独标注。', unhealthy.length ? 'error' : '');
    const coverage = document.createElement('p');
    coverage.className = 'watch-sync';
    coverage.textContent = discovery.coverage;
    section.append(heading, status, coverage);
    for (const source of unhealthy) {
      const warning = document.createElement('p');
      warning.className = 'watch-sync has-error';
      warning.textContent = `${source.name}：暂未完成更新${source.lastSuccessAt ? `，上次成功 ${this.formatClock(source.lastSuccessAt)}` : '，正在核验官方来源'}`;
      section.append(warning);
    }
    const list = document.createElement('div');
    list.className = 'watch-event-list';
    for (const event of discovery.events) {
      const card = document.createElement('article');
      card.className = 'watch-event-card official-agenda';
      const date = formatEventDate(event.date, event.time);
      const dateBlock = document.createElement('div');
      dateBlock.className = 'watch-event-date';
      const month = document.createElement('span');
      month.textContent = date.month;
      const day = document.createElement('b');
      day.textContent = date.day;
      dateBlock.append(month, day);
      const content = document.createElement('div');
      content.className = 'watch-event-content';
      const title = this.headline(`watch:calendar:${event.key}`, event);
      const time = document.createElement('p');
      time.textContent = event.time ? `${date.full}（北京时间）` : `官方当地日期 ${event.date}；具体时间及北京时间待确认`;
      const verified = document.createElement('p');
      verified.className = 'watch-sync';
      verified.textContent = `最近核实 ${this.formatClock(event.checkedAt)}`;
      content.append(title, time, verified);
      try {
        const url = new URL(event.source.url);
        if (url.protocol === 'https:' && ['www.federalreserve.gov', 'federalreserve.gov', 'www.frbsf.org', 'frbsf.org'].includes(url.hostname)) {
          const links = sourceReadingLinks(url.href, event.source.name);
          if (links) content.append(links);
        }
      } catch { /* Keep the confirmed text visible if a source URL is unavailable. */ }
      card.append(dateBlock, content);
      list.append(card);
    }
    if (!discovery.events.length) list.append(this.message(discovery.enabled ? '暂无已核实时间的近期会议，继续关注官方日程更新。' : '重新启用后会继续核对官方会议。'));
    section.append(list);
    return section;
  }

  findHeadline(ref: string): HeadlineTarget | null {
    const row = this.titles.get(ref);
    return row ? { ref, title: row.title, translated_title: row.translated_title || this.completed(row.title) } : null;
  }

  acceptTranslation(original: string, translated: string, provider: string): void {
    this.titles.forEach((row) => {
      if (row.title === original) {
        row.translated_title = translated;
        row.translation_provider = provider;
      }
    });
    this.updateTitles(this.useChinese);
  }

  updateTitles(enabled: boolean): void {
    this.useChinese = enabled;
    this.element.querySelectorAll<HTMLElement>('[data-watch-headline-ref]').forEach((node) => {
      const row = this.titles.get(node.dataset.watchHeadlineRef || '');
      if (!row) return;
      const translated = enabled ? row.translated_title || this.completed(row.title) : '';
      node.textContent = translated || row.title;
      const original = node.parentElement?.querySelector<HTMLElement>('.news-original-title');
      if (original) {
        original.textContent = translated && translated !== row.title ? row.title : '';
        original.classList.toggle('hidden', !original.textContent);
      }
    });
  }

  private headline(ref: string, row: TitleRecord, tag = 'h3'): HTMLElement {
    this.titles.set(ref, row);
    const box = document.createElement('div');
    const title = document.createElement(tag);
    title.dataset.watchHeadlineRef = ref;
    const translated = this.useChinese ? row.translated_title || this.completed(row.title) : '';
    title.textContent = translated || row.title;
    const original = document.createElement('p');
    original.className = 'news-original-title';
    original.textContent = translated && translated !== row.title ? row.title : '';
    original.classList.toggle('hidden', !original.textContent);
    box.append(title, original);
    return box;
  }

  private metric(label: string, value: number): HTMLElement {
    const metric = document.createElement('div');
    const number = document.createElement('b');
    number.textContent = String(value);
    const name = document.createElement('span');
    name.textContent = label;
    metric.append(number, name);
    return metric;
  }

  private eventCard(event: WatchEvent): HTMLElement {
    const card = document.createElement('article');
    card.className = `watch-event-card scope-${event.scope} official-${event.official_status}${event.match_count ? ' has-match' : ''}`;

    const date = formatEventDate(event.event_date, event.event_time);
    const dateBlock = document.createElement('div');
    dateBlock.className = 'watch-event-date';
    const month = document.createElement('span');
    month.textContent = date.month;
    const day = document.createElement('b');
    day.textContent = date.day;
    dateBlock.append(month, day);

    const content = document.createElement('div');
    content.className = 'watch-event-content';
    const badges = document.createElement('div');
    badges.className = 'watch-event-badges';
    const scope = document.createElement('span');
    scope.className = `watch-scope scope-${event.scope}`;
    scope.textContent = scopeLabel(event.scope);
    const monitor = document.createElement('span');
    monitor.className = `watch-monitor official-${event.official_status}`;
    monitor.textContent = event.monitor_status;
    badges.append(scope, monitor);
    if (event.candidate_status) {
      const candidate = document.createElement('span');
      candidate.className = 'watch-candidate';
      candidate.textContent = event.candidate_status;
      badges.append(candidate);
    }
    if (event.latest_signal || event.analysis_feedback?.status) {
      const pipeline = document.createElement('span');
      pipeline.className = event.analysis_feedback?.status
        ? `watch-pipeline compass-${event.analysis_feedback.status}`
        : `watch-pipeline signal-${event.latest_signal?.status}`;
      pipeline.textContent = this.pipelineLabel(event);
      badges.append(pipeline);
    }

    const title = this.headline(`watch:event:${event.event_key}`, event);
    const meta = document.createElement('p');
    meta.className = 'watch-event-meta';
    meta.textContent = `${date.full} · ${event.category || '重点事件'} · 重要性 ${event.importance}/5`;
    content.append(badges, title, meta);

    if (event.analysis_feedback?.status) {
      const feedback = document.createElement('p');
      feedback.className = `watch-analysis-feedback compass-${event.analysis_feedback.status}`;
      const attempts = ['retrying', 'failed'].includes(event.analysis_feedback.status)
        ? `（第 ${event.analysis_feedback.attemptCount}/${event.analysis_feedback.maxAttempts} 次）`
        : '';
      feedback.textContent = `${event.pipeline_status}${attempts}`;
      content.append(feedback);
    }

    if (event.monitoring?.coverage === 'verified') {
      content.append(this.officialMonitor(event));
    }

    if (event.note) {
      const note = document.createElement('p');
      note.className = 'watch-event-note';
      note.textContent = event.note;
      content.append(note);
    }

    if (event.latest_matches.length > 0) {
      const matches = document.createElement('div');
      matches.className = 'watch-matches';
      const label = document.createElement('strong');
      label.textContent = `监测到 ${event.match_count} 条候选消息（不等同于官方结果）`;
      matches.append(label);
      event.latest_matches.forEach((match) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.itemId = String(match.item_id);
        const headline = this.headline(`news:${match.item_id}`, match, 'b');
        const detail = document.createElement('span');
        detail.textContent = `${this.formatClock(match.published_at || match.first_seen_at)} · 命中 ${match.matched_terms.slice(0, 3).join('、')}`;
        button.append(headline, detail);
        matches.append(button);
      });
      content.append(matches);
    }

    card.append(dateBlock, content);
    return card;
  }

  private officialMonitor(event: WatchEvent): HTMLElement {
    const box = document.createElement('div');
    box.className = 'watch-official';
    const facts = document.createElement('div');
    facts.className = 'watch-official-facts';
    facts.append(
      this.fact('发布方', event.monitoring.publisher.name),
      this.fact('发布时间', event.monitoring.release.label),
      this.fact('监测窗口', this.formatRange(event.monitoring.release.windowStart, event.monitoring.release.windowEnd)),
    );
    const channels = document.createElement('div');
    channels.className = 'watch-official-channels';
    event.official_channels.forEach((channel) => {
      const row = document.createElement('div');
      row.className = 'watch-source-channel';
      const name = document.createElement('b');
      name.textContent = channel.name;
      const status = document.createElement('span');
      status.className = channel.last_error ? 'has-error' : channel.last_success_at ? 'is-ready' : '';
      status.textContent = channel.last_error
        ? '检查异常，系统会自动重试'
        : channel.last_success_at
          ? `官方页面可达 · ${this.formatClock(channel.last_success_at)}`
          : `待首次检查${channel.next_check_at ? ` · ${this.formatClock(channel.next_check_at)}` : ''}`;
      row.append(name, status);
      const links = sourceReadingLinks(channel.url, '官方渠道');
      if (links) row.append(links);
      channels.append(row);
    });
    if (!event.official_channels.length) {
      const pending = document.createElement('p');
      pending.textContent = '官方渠道配置正在同步。';
      channels.append(pending);
    }
    box.append(facts, channels);
    return box;
  }

  private pipelineLabel(event: WatchEvent): string {
    const labels: Record<string, string> = {
      received: '罗盘已收到',
      analyzing: 'Codex 分析中',
      retrying: 'Codex 自动重试',
      published: '报告已回填',
      skipped: '核验后跳过',
      failed: '需要人工复查',
    };
    return event.analysis_feedback?.status
      ? labels[event.analysis_feedback.status] || '罗盘处理中'
      : event.pipeline_status;
  }

  private fact(label: string, value: string): HTMLElement {
    const node = document.createElement('div');
    const name = document.createElement('span');
    name.textContent = label;
    const content = document.createElement('b');
    content.textContent = value || '待核验';
    node.append(name, content);
    return node;
  }

  private formatRange(start: string, end: string): string {
    if (!start || !end) return '待核验';
    const format = (value: string): string => {
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return value;
      return new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
      }).format(parsed);
    };
    return `${format(start)}—${format(end)}`;
  }

  private formatClock(value: string): string {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(parsed);
  }

  private message(text: string, className = ''): HTMLElement {
    const node = document.createElement('div');
    node.className = `panel-message ${className}`.trim();
    node.textContent = text;
    return node;
  }
}
