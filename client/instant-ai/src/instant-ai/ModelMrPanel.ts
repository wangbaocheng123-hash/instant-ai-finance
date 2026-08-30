import { instantApi } from './api';
import type { ModelMrChatConfig, ModelMrThoughtCategory, ModelMrWork } from './types';

type ModelMrTab = 'works' | 'thoughts' | 'chat';

export class ModelMrPanel {
  public readonly element: HTMLElement;
  private readonly body: HTMLElement;
  private readonly badge: HTMLElement;
  private activeTab: ModelMrTab = 'works';
  private works: ModelMrWork[] = [];
  private thoughts: ModelMrThoughtCategory[] = [];
  private chatConfig: ModelMrChatConfig | null = null;
  private chatMessages: Array<{ role: 'user' | 'assistant'; content: string }> = [];
  private available = false;
  private sending = false;

  constructor() {
    this.element = document.createElement('article');
    this.element.className = 'finance-panel model-mr-panel';
    this.element.dataset.section = 'model-mr';
    this.element.hidden = true;
    this.element.innerHTML = `
      <header class="panel-header model-mr-header">
        <div class="panel-heading"><h2>模型先生</h2><span>独立模块 · 精简手机版</span></div>
        <span class="panel-count">连接中</span>
      </header>
      <nav class="model-mr-tabs" aria-label="模型先生功能">
        <button type="button" data-model-tab="works" class="is-active">作品</button>
        <button type="button" data-model-tab="thoughts">投资思路</button>
        <button type="button" data-model-tab="chat">智能问答</button>
      </nav>
      <div class="panel-body model-mr-body"><div class="panel-message">正在连接模型先生本机服务…</div></div>`;
    this.body = this.required('.model-mr-body');
    this.badge = this.required('.panel-count');
    this.element.addEventListener('click', (event) => {
      const tab = (event.target as HTMLElement).closest<HTMLElement>('[data-model-tab]')?.dataset.modelTab as ModelMrTab | undefined;
      if (tab) this.selectTab(tab);
    });
    this.element.addEventListener('submit', (event) => {
      if ((event.target as HTMLElement).id !== 'modelMrChatForm') return;
      event.preventDefault();
      void this.sendChat();
    });
  }

  async refresh(): Promise<void> {
    const status = await instantApi.modelMrStatus();
    this.available = status.available;
    if (!status.available) {
      this.badge.textContent = '未连接';
      this.renderUnavailable(status.message);
      return;
    }
    const [works, thoughts, chatConfig] = await Promise.all([
      instantApi.modelMrWorks(48), instantApi.modelMrThoughts(), instantApi.modelMrChatConfig(),
    ]);
    this.works = works.items;
    this.thoughts = thoughts.categories;
    this.chatConfig = chatConfig;
    this.badge.textContent = `${status.counts?.works ?? works.count} 部`;
    this.renderActiveTab();
  }

  setError(message: string): void {
    this.available = false;
    this.badge.textContent = '异常';
    this.renderUnavailable(message);
  }

  private selectTab(tab: ModelMrTab): void {
    this.activeTab = tab;
    this.element.querySelectorAll<HTMLElement>('[data-model-tab]').forEach((button) => {
      const active = button.dataset.modelTab === tab;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', String(active));
    });
    this.renderActiveTab();
  }

  private renderActiveTab(): void {
    if (!this.available) return;
    if (this.activeTab === 'works') this.renderWorks();
    else if (this.activeTab === 'thoughts') this.renderThoughts();
    else this.renderChat();
  }

  private renderWorks(): void {
    const list = document.createElement('div');
    list.className = 'model-work-list';
    this.works.slice(0, 24).forEach((work) => {
      const card = document.createElement('article');
      card.className = 'model-work-card';
      const date = document.createElement('time');
      date.textContent = this.formatDate(work.published_at);
      const title = document.createElement('h3');
      title.textContent = work.title;
      const meta = document.createElement('div');
      meta.className = 'model-work-meta';
      if (work.has_video_text) meta.append(this.pill('有视频原文'));
      if (work.has_interpretation) meta.append(this.pill('有解读'));
      work.keywords.slice(0, 3).forEach((keyword) => meta.append(this.pill(keyword)));
      card.append(date, title, meta);
      if (work.description) {
        const description = document.createElement('p');
        description.textContent = work.description;
        card.append(description);
      }
      if (work.url) {
        const link = document.createElement('a');
        link.href = work.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = '查看原作品';
        card.append(link);
      }
      list.append(card);
    });
    if (!this.works.length) list.append(this.message('模型先生作品库当前没有可显示内容。'));
    this.body.replaceChildren(list);
  }

  private renderThoughts(): void {
    const root = document.createElement('div');
    root.className = 'model-thought-list';
    const parents = this.thoughts.filter((category) => category.level === 1);
    parents.forEach((parent) => {
      const section = document.createElement('section');
      const heading = document.createElement('header');
      const title = document.createElement('h3');
      title.textContent = parent.name;
      const count = document.createElement('span');
      count.textContent = `${parent.video_count} 部作品`;
      heading.append(title, count);
      if (parent.description) {
        const description = document.createElement('p');
        description.textContent = parent.description;
        heading.append(description);
      }
      const children = document.createElement('div');
      children.className = 'model-thought-children';
      this.thoughts.filter((item) => item.parent_id === parent.id).forEach((child) => {
        const item = document.createElement('div');
        const name = document.createElement('b');
        name.textContent = child.name;
        const total = document.createElement('span');
        total.textContent = `${child.video_count}`;
        item.append(name, total);
        children.append(item);
      });
      section.append(heading, children);
      root.append(section);
    });
    if (!parents.length) root.append(this.message('投资思路索引当前没有可显示内容。'));
    this.body.replaceChildren(root);
  }

  private renderChat(): void {
    const shell = document.createElement('div');
    shell.className = 'model-chat-shell';
    const messages = document.createElement('div');
    messages.className = 'model-chat-messages';
    if (!this.chatMessages.length) {
      const welcome = document.createElement('article');
      welcome.className = 'model-chat-message assistant';
      welcome.textContent = '可以询问模型先生已经保存的作品原文、投资观点和历史判断。回答会区分原始观点与 AI 分析。';
      messages.append(welcome);
    }
    this.chatMessages.forEach((entry) => {
      const message = document.createElement('article');
      message.className = `model-chat-message ${entry.role}`;
      message.textContent = entry.content;
      messages.append(message);
    });

    const form = document.createElement('form');
    form.id = 'modelMrChatForm';
    form.className = 'model-chat-form';
    const select = document.createElement('select');
    select.id = 'modelMrChatModel';
    select.setAttribute('aria-label', '模型先生对话模型');
    (this.chatConfig?.models || []).forEach((model) => {
      const option = document.createElement('option');
      option.value = model.id;
      option.textContent = model.label;
      option.selected = model.id === this.chatConfig?.default_model;
      select.append(option);
    });
    const input = document.createElement('textarea');
    input.id = 'modelMrChatInput';
    input.rows = 3;
    input.maxLength = 6000;
    input.placeholder = '例如：模型先生最近如何看待科技股？';
    const button = document.createElement('button');
    button.type = 'submit';
    button.textContent = this.sending ? '正在回答…' : '发送';
    const enabled = this.chatConfig?.enabled === true && !this.sending;
    select.disabled = !enabled;
    input.disabled = !enabled;
    button.disabled = !enabled;
    const note = document.createElement('p');
    note.textContent = this.chatConfig?.message || '模型先生智能问答当前未连接。';
    form.append(select, input, button, note);
    shell.append(messages, form);
    this.body.replaceChildren(shell);
    messages.scrollTop = messages.scrollHeight;
  }

  private async sendChat(): Promise<void> {
    if (this.sending || !this.chatConfig?.enabled) return;
    const input = this.required<HTMLTextAreaElement>('#modelMrChatInput');
    const content = input.value.trim();
    if (!content) return;
    const model = this.required<HTMLSelectElement>('#modelMrChatModel').value || this.chatConfig.default_model;
    this.chatMessages.push({ role: 'user', content });
    this.sending = true;
    this.renderChat();
    try {
      const result = await instantApi.modelMrChat(this.chatMessages, model);
      this.chatMessages.push({ role: 'assistant', content: result.answer });
    } catch (error) {
      this.chatMessages.push({ role: 'assistant', content: error instanceof Error ? `暂时无法回答：${error.message}` : '暂时无法回答。' });
    } finally {
      this.sending = false;
      this.renderChat();
    }
  }

  private renderUnavailable(message: string): void {
    const box = document.createElement('div');
    box.className = 'model-mr-unavailable';
    const mark = document.createElement('span');
    mark.textContent = '模';
    const title = document.createElement('h3');
    title.textContent = '模型先生模块暂未连接';
    const text = document.createElement('p');
    text.textContent = message || '请确认模型先生本机服务正在运行。';
    const boundary = document.createElement('small');
    boundary.textContent = '独立模块不会把粉丝资料、导入工具、删除编辑或 API 密钥写入即时 AI。';
    box.append(mark, title, text, boundary);
    this.body.replaceChildren(box);
  }

  private pill(text: string): HTMLElement {
    const element = document.createElement('span');
    element.textContent = text;
    return element;
  }

  private message(text: string): HTMLElement {
    const element = document.createElement('div');
    element.className = 'panel-message';
    element.textContent = text;
    return element;
  }

  private formatDate(value: string): string {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value || '时间待确认';
    return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
  }

  private required<T extends HTMLElement = HTMLElement>(selector: string): T {
    const value = this.element.querySelector<T>(selector);
    if (!value) throw new Error(`模型先生界面缺少元素：${selector}`);
    return value;
  }
}
