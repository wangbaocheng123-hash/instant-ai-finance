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
      this.metric('已匹配', response.counts.matched),
    );
    const sync = document.createElement('p');
    sync.className = response.sync?.last_error ? 'watch-sync has-error' : 'watch-sync';
    sync.textContent = response.sync?.last_error
      ? '罗盘本轮暂未连通，继续使用上次同步列表监测。'
      : `即时AI每 5 分钟比对一次财经消息${response.sync?.last_success_at ? ` · 最近同步 ${this.formatClock(response.sync.last_success_at)}` : ''}`;

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
    card.className = `watch-event-card scope-${event.scope}${event.match_count ? ' has-match' : ''}`;

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
    monitor.className = event.match_count ? 'watch-monitor has-match' : 'watch-monitor';
    monitor.textContent = event.monitor_status;
    badges.append(scope, monitor);

    const title = document.createElement('h3');
    title.textContent = event.title;
    const meta = document.createElement('p');
    meta.className = 'watch-event-meta';
    meta.textContent = `${date.full} · ${event.category || '重点事件'} · 重要性 ${event.importance}/5`;
    content.append(badges, title, meta);

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
      label.textContent = `监测到 ${event.match_count} 条相关消息`;
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
