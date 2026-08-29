import type { WatchEvent, WatchEventsResponse } from './types';

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

  constructor() {
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

  render(response: WatchEventsResponse): void {
    this.count.textContent = String(response.counts.total);
    const summary = document.createElement('div');
    summary.className = 'watch-summary';
    summary.append(
      this.metric('全部', response.counts.total),
      this.metric('首页', response.counts.home),
      this.metric('紫金', response.counts.zijin),
      this.metric('官方正常', response.counts.official_reachable),
    );
    const sync = document.createElement('p');
    sync.className = response.sync?.last_error ? 'watch-sync has-error' : 'watch-sync';
    sync.textContent = response.sync?.last_error
      ? '罗盘本轮暂未连通，继续使用上次同步列表监测。'
      : `已配置 ${response.counts.configured} 个事件的官方渠道；即时AI每 5 分钟检查到期渠道并比对财经消息${response.sync?.last_success_at ? ` · 最近同步 ${this.formatClock(response.sync.last_success_at)}` : ''}`;

    const list = document.createElement('div');
    list.className = 'watch-event-list';
    response.events.forEach((event) => list.append(this.eventCard(event)));
    if (response.events.length === 0) list.append(this.message('罗盘尚未同步重点事件，连接后会自动出现。'));
    this.body.replaceChildren(summary, sync, list);
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

    const title = document.createElement('h3');
    title.textContent = event.title;
    const meta = document.createElement('p');
    meta.className = 'watch-event-meta';
    meta.textContent = `${date.full} · ${event.category || '重点事件'} · 重要性 ${event.importance}/5`;
    content.append(badges, title, meta);

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
        const headline = document.createElement('b');
        headline.textContent = match.translated_title?.trim() || match.title;
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
      const link = document.createElement('a');
      link.href = channel.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const name = document.createElement('b');
      name.textContent = channel.name;
      const status = document.createElement('span');
      status.className = channel.last_error ? 'has-error' : channel.last_success_at ? 'is-ready' : '';
      status.textContent = channel.last_error
        ? '检查异常，系统会自动重试'
        : channel.last_success_at
          ? `官方页面可达 · ${this.formatClock(channel.last_success_at)}`
          : `待首次检查${channel.next_check_at ? ` · ${this.formatClock(channel.next_check_at)}` : ''}`;
      link.append(name, status);
      channels.append(link);
    });
    if (!event.official_channels.length) {
      const pending = document.createElement('p');
      pending.textContent = '官方渠道配置正在同步。';
      channels.append(pending);
    }
    box.append(facts, channels);
    return box;
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
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(parsed);
  }

  private message(text: string, className = ''): HTMLElement {
    const node = document.createElement('div');
    node.className = `panel-message ${className}`.trim();
    node.textContent = text;
    return node;
  }
}
