import type { FinanceItem, SectionDefinition } from './types';

const formatTime = (value: string | null): string => {
  if (!value) return '时间待确认';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(parsed);
};

export class FinancePanel {
  public readonly element: HTMLElement;
  private readonly body: HTMLElement;
  private readonly count: HTMLElement;
  private readonly activity: HTMLElement;
  private readonly definition: SectionDefinition;

  constructor(definition: SectionDefinition) {
    this.definition = definition;
    this.element = document.createElement('section');
    this.element.className = 'finance-panel';
    this.element.dataset.panel = definition.id;
    this.element.style.setProperty('--panel-accent', definition.accent);

    const header = document.createElement('header');
    header.className = 'panel-header';
    const heading = document.createElement('div');
    heading.className = 'panel-heading';
    const title = document.createElement('h2');
    title.textContent = definition.title;
    const subtitle = document.createElement('span');
    subtitle.textContent = definition.subtitle;
    heading.append(title, subtitle);

    this.activity = document.createElement('b');
    this.activity.className = 'activity-badge hidden';
    this.activity.textContent = '新';
    this.count = document.createElement('strong');
    this.count.className = 'panel-count';
    this.count.textContent = '0';
    header.append(heading, this.activity, this.count);

    this.body = document.createElement('div');
    this.body.className = 'panel-body';
    this.element.append(header, this.body);
  }

  setLoading(): void {
    this.body.replaceChildren(this.message('正在读取财经资讯库…'));
  }

  setError(message: string): void {
    this.body.replaceChildren(this.message(message, 'error'));
  }

  render(items: FinanceItem[], useChineseTitles = true): void {
    this.count.textContent = String(items.length);
    this.markActivity(items);
    if (items.length === 0) {
      this.body.replaceChildren(this.message('暂时没有匹配消息，下一轮采集后自动更新。'));
      return;
    }
    const fragment = document.createDocumentFragment();
    items.slice(0, 12).forEach((item) => fragment.append(this.renderItem(item, useChineseTitles)));
    this.body.replaceChildren(fragment);
  }

  private message(text: string, className = ''): HTMLElement {
    const node = document.createElement('div');
    node.className = `panel-message ${className}`.trim();
    node.textContent = text;
    return node;
  }

  private renderItem(item: FinanceItem, useChineseTitles: boolean): HTMLElement {
    const button = document.createElement('button');
    button.className = `news-row${item.is_read ? ' is-read' : ''}`;
    button.type = 'button';
    button.dataset.itemId = String(item.id);

    const thumbnail = document.createElement('img');
    thumbnail.className = 'news-thumbnail';
    thumbnail.src = item.thumbnail_url;
    thumbnail.alt = '';
    thumbnail.loading = 'lazy';
    thumbnail.decoding = 'async';

    const content = document.createElement('div');
    content.className = 'news-content';

    const meta = document.createElement('div');
    meta.className = 'news-meta';
    const source = document.createElement('span');
    source.className = 'news-source';
    source.textContent = item.sources?.[0] || item.event_type;
    const time = document.createElement('time');
    time.textContent = formatTime(item.published_at || item.first_seen_at);
    const score = document.createElement('b');
    score.className = `score score-${item.importance_score >= 85 ? 'critical' : item.importance_score >= 70 ? 'high' : 'normal'}`;
    score.textContent = String(item.importance_score);
    meta.append(source, time, score);

    const title = document.createElement('h3');
    const translatedTitle = useChineseTitles ? item.translated_title?.trim() : '';
    title.textContent = translatedTitle || item.title;
    const originalTitle = document.createElement('p');
    originalTitle.className = 'news-original-title';
    originalTitle.textContent = translatedTitle && translatedTitle !== item.title ? item.title : '';
    originalTitle.classList.toggle('hidden', !originalTitle.textContent);
    const tags = document.createElement('div');
    tags.className = 'news-tags';
    item.topics.slice(0, 3).forEach((topic) => {
      const tag = document.createElement('span');
      tag.textContent = topic;
      tags.append(tag);
    });
    content.append(meta, title, originalTitle, tags);
    button.append(thumbnail, content);
    return button;
  }

  private markActivity(items: FinanceItem[]): void {
    const key = `instant-ai-seen-${this.definition.id}`;
    const current = items.map((item) => item.id);
    let previous: number[] = [];
    try {
      previous = JSON.parse(localStorage.getItem(key) || '[]') as number[];
    } catch {
      previous = [];
    }
    const previousSet = new Set(previous);
    const newCount = previous.length === 0 ? 0 : current.filter((id) => !previousSet.has(id)).length;
    this.activity.textContent = newCount > 0 ? `${newCount} 新` : '新';
    this.activity.classList.toggle('hidden', newCount === 0);
    localStorage.setItem(key, JSON.stringify(current.slice(0, 80)));
  }
}
