import { instantApi } from './api';
import type {
  ModelMrChatConfig, ModelMrComment, ModelMrThoughtCategory, ModelMrWork, ModelMrWorkDetail,
} from './types';

type ModelMrTab = 'works' | 'thoughts' | 'chat';
type WorkDetailTab = 'video' | 'text' | 'comments';
type CommentTab = 'author' | 'ranking' | 'stocks';

interface ModelMrCommentThread {
  key: string;
  root: ModelMrComment | null;
  replies: ModelMrComment[];
}

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
  private readonly details = new Map<number, ModelMrWorkDetail>();
  private readonly detailTabs = new Map<number, WorkDetailTab>();
  private readonly commentTabs = new Map<number, CommentTab>();
  private readonly commentLimits = new Map<number, number>();
  private readonly editingTitles = new Set<number>();
  private readonly workMessages = new Map<number, { text: string; tone: string }>();
  private readonly busyWorks = new Set<number>();

  constructor() {
    this.element = document.createElement('article');
    this.element.className = 'finance-panel model-mr-panel';
    this.element.dataset.section = 'model-mr';
    this.element.hidden = true;
    this.element.innerHTML = `
      <header class="panel-header model-mr-header">
        <div class="panel-heading"><h2>模型先生</h2><span>主人手机版 · 视频、原文与评论</span></div>
        <span class="panel-count">连接中</span>
      </header>
      <nav class="model-mr-tabs" aria-label="模型先生功能">
        <button type="button" data-model-tab="works" class="is-active">作品</button>
        <button type="button" data-model-tab="thoughts">投资思路</button>
        <button type="button" data-model-tab="chat">智能问答</button>
      </nav>
      <div class="panel-body model-mr-body"><div class="panel-message">正在连接模型先生主人资料库…</div></div>`;
    this.body = this.required('.model-mr-body');
    this.badge = this.required('.panel-count');
    this.element.addEventListener('click', (event) => void this.handleClick(event));
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
      instantApi.modelMrWorks(120), instantApi.modelMrThoughts(), instantApi.modelMrChatConfig(),
    ]);
    this.works = works.items;
    this.thoughts = thoughts.categories;
    this.chatConfig = chatConfig;
    const media = status.counts?.media ?? 0;
    this.badge.textContent = media ? `${status.counts?.works ?? works.count} 部 · ${media} 视频` : `${status.counts?.works ?? works.count} 部`;
    this.renderActiveTab();
  }

  setError(message: string): void {
    this.available = false;
    this.badge.textContent = '异常';
    this.renderUnavailable(message);
  }

  private async handleClick(event: MouseEvent): Promise<void> {
    const target = event.target as HTMLElement;
    const sectionTab = target.closest<HTMLElement>('[data-model-tab]')?.dataset.modelTab as ModelMrTab | undefined;
    if (sectionTab) {
      this.selectTab(sectionTab);
      return;
    }
    const action = target.closest<HTMLElement>('[data-model-action]');
    if (!action) return;
    const workId = Number(action.dataset.workId || 0);
    if (!workId) return;
    const command = action.dataset.modelAction;
    if (command === 'open-detail') await this.openWorkDetail(workId, (action.dataset.detailTab || 'video') as WorkDetailTab);
    else if (command === 'detail-tab') this.setWorkDetailTab(workId, (action.dataset.detailTab || 'video') as WorkDetailTab);
    else if (command === 'close-detail') this.closeWorkDetail(workId);
    else if (command === 'save-text') await this.saveVideoText(workId);
    else if (command === 'transcribe') await this.transcribe(workId, 'video');
    else if (command === 'doubao') await this.transcribe(workId, 'doubao');
    else if (command === 'more-comments') this.showMoreComments(workId);
    else if (command === 'edit-title') this.editTitle(workId);
    else if (command === 'cancel-title') this.cancelTitle(workId);
    else if (command === 'save-title') await this.saveTitle(workId);
    else if (command === 'comment-tab') this.setCommentTab(workId, (action.dataset.commentTab || 'author') as CommentTab);
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
    list.className = 'model-work-list model-work-list-full';
    this.works.slice(0, 80).forEach((work) => list.append(this.renderWorkCard(work)));
    if (!this.works.length) list.append(this.message('模型先生作品库当前没有可显示内容。'));
    this.body.replaceChildren(list);
  }

  private renderWorkCard(work: ModelMrWork): HTMLElement {
    const card = document.createElement('article');
    card.className = 'model-work-card model-work-card-full';
    card.dataset.workId = String(work.id);
    const heading = document.createElement('div');
    heading.className = 'model-work-heading';
    const titleGroup = document.createElement('div');
    titleGroup.className = 'model-work-title-group';
    const title = document.createElement('h3');
    title.textContent = work.title;
    const editTitle = this.actionButton('改标题', work.id, 'edit-title');
    editTitle.classList.add('model-title-edit-button');
    editTitle.disabled = this.busyWorks.has(work.id);
    titleGroup.append(title, editTitle);
    const date = document.createElement('time');
    date.textContent = this.formatDate(work.published_at);
    heading.append(titleGroup, date);
    const meta = document.createElement('div');
    meta.className = 'model-work-meta';
    if (work.media_available) meta.append(this.pill('本地视频'));
    if (work.has_video_text) meta.append(this.pill('有视频原文'));
    if (work.has_interpretation) meta.append(this.pill('有解读'));
    if (work.comment_count) meta.append(this.pill(`${work.comment_count} 条评论`));
    work.keywords.slice(0, 3).forEach((keyword) => meta.append(this.pill(keyword)));
    card.append(heading);
    if (this.editingTitles.has(work.id)) {
      const editor = document.createElement('div');
      editor.className = 'model-title-editor';
      const input = document.createElement('input');
      input.id = `model-title-${work.id}`;
      input.value = work.title;
      input.maxLength = 120;
      input.setAttribute('aria-label', '作品标题');
      const save = this.actionButton('保存标题', work.id, 'save-title', undefined, true);
      const cancel = this.actionButton('取消', work.id, 'cancel-title');
      editor.append(input, save, cancel);
      card.append(editor);
    }
    card.append(meta);
    if (work.description) {
      const description = document.createElement('p');
      description.textContent = work.description;
      card.append(description);
    }
    const actions = document.createElement('div');
    actions.className = 'model-work-actions';
    if (work.media_available) actions.append(this.actionButton('播放本地视频', work.id, 'open-detail', 'video', true));
    actions.append(this.actionButton('视频原文', work.id, 'open-detail', 'text'));
    actions.append(this.actionButton(`评论 ${work.comment_count || ''}`.trim(), work.id, 'open-detail', 'comments'));
    if (work.url) {
      const link = document.createElement('a');
      link.className = 'model-original-link';
      link.href = work.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = '抖音原链接';
      actions.append(link);
    }
    card.append(actions);
    const detail = this.details.get(work.id);
    if (detail) card.append(this.renderWorkDetail(detail));
    else {
      const status = this.workMessages.get(work.id);
      if (status || this.busyWorks.has(work.id)) {
        const message = document.createElement('p');
        message.className = `model-work-status ${status?.tone || ''}`;
        message.textContent = status?.text || '正在处理…';
        card.append(message);
      }
    }
    return card;
  }

  private async openWorkDetail(workId: number, tab: WorkDetailTab): Promise<void> {
    this.detailTabs.set(workId, tab);
    if (!this.details.has(workId)) {
      this.busyWorks.add(workId);
      this.workMessages.delete(workId);
      this.setWorkMessage(workId, '正在读取本地作品资料…', '');
      this.renderWorks();
      try {
        this.details.set(workId, await instantApi.modelMrWork(workId));
        this.workMessages.delete(workId);
      } catch (error) {
        this.setWorkMessage(workId, error instanceof Error ? error.message : '作品资料读取失败。', 'is-error');
      } finally {
        this.busyWorks.delete(workId);
      }
    }
    this.renderWorks();
    requestAnimationFrame(() => this.element.querySelector<HTMLElement>(`[data-work-id="${workId}"] .model-work-detail`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  }

  private closeWorkDetail(workId: number): void {
    this.details.delete(workId);
    this.renderWorks();
  }

  private setWorkDetailTab(workId: number, tab: WorkDetailTab): void {
    this.detailTabs.set(workId, tab);
    this.renderWorks();
  }

  private renderWorkDetail(detail: ModelMrWorkDetail): HTMLElement {
    const workId = detail.work.id;
    const tab = this.detailTabs.get(workId) || (detail.work.media_available ? 'video' : 'text');
    const shell = document.createElement('section');
    shell.className = 'model-work-detail';
    const tabs = document.createElement('nav');
    tabs.className = 'model-detail-tabs';
    (['video', 'text', 'comments'] as WorkDetailTab[]).forEach((value) => {
      const labels: Record<WorkDetailTab, string> = { video: '本地视频', text: '视频原文', comments: `评论 ${detail.comment_total}` };
      const button = this.actionButton(labels[value], workId, 'detail-tab', value);
      button.classList.toggle('is-active', value === tab);
      tabs.append(button);
    });
    tabs.append(this.actionButton('收起', workId, 'close-detail'));
    const content = document.createElement('div');
    content.className = 'model-detail-content';
    if (tab === 'video') content.append(this.renderVideo(detail));
    else if (tab === 'text') content.append(this.renderVideoText(detail));
    else content.append(this.renderComments(detail));
    const status = this.workMessages.get(workId);
    if (status || this.busyWorks.has(workId)) {
      const message = document.createElement('p');
      message.className = `model-work-status ${status?.tone || ''}`;
      message.textContent = status?.text || '正在处理…';
      content.append(message);
    }
    shell.append(tabs, content);
    return shell;
  }

  private renderVideo(detail: ModelMrWorkDetail): HTMLElement {
    const panel = document.createElement('div');
    panel.className = 'model-video-panel';
    if (detail.work.media_available && detail.work.video_url) {
      const video = document.createElement('video');
      video.controls = true;
      video.playsInline = true;
      video.preload = 'metadata';
      video.src = detail.work.video_url;
      const note = document.createElement('p');
      note.textContent = '正在读取视频信息…';
      video.addEventListener('loadedmetadata', () => {
        const seconds = Number.isFinite(video.duration) ? Math.max(1, Math.round(video.duration)) : 0;
        note.textContent = `本地有声视频已就绪${seconds ? ` · ${Math.floor(seconds / 60)}分${seconds % 60}秒` : ''}，不会跳转抖音。`;
      });
      video.addEventListener('error', () => {
        note.textContent = '本地视频加载失败。请先确认网络正常，再收起后重新打开；错误不会跳转抖音。';
        note.classList.add('is-error');
      });
      panel.append(video, note);
    } else panel.append(this.message('这条作品暂未匹配到本地视频，可使用抖音原链接查看来源。'));
    return panel;
  }

  private renderVideoText(detail: ModelMrWorkDetail): HTMLElement {
    const panel = document.createElement('div');
    panel.className = 'model-video-text-panel';
    const text = document.createElement('textarea');
    text.id = `model-video-text-${detail.work.id}`;
    text.value = detail.video_text.text || detail.transcripts[0]?.text || '';
    text.placeholder = '尚无视频原文，可点击下方识别按钮载入或生成文字。';
    text.maxLength = 200000;
    const source = document.createElement('p');
    source.className = 'model-text-source';
    source.textContent = detail.video_text.source ? `当前来源：${detail.video_text.source}${detail.video_text.official ? '（正式原文）' : ''}` : '识别结果请核对后保存为正式原文。';
    const actions = document.createElement('div');
    actions.className = 'model-text-actions';
    const busy = this.busyWorks.has(detail.work.id);
    const videoButton = this.actionButton('识别视频文字', detail.work.id, 'transcribe');
    const doubaoButton = this.actionButton('豆包识别文字', detail.work.id, 'doubao', undefined, true);
    const saveButton = this.actionButton('保存正式原文', detail.work.id, 'save-text');
    videoButton.disabled = busy || !detail.capabilities.transcribe_video;
    doubaoButton.disabled = busy || !detail.capabilities.doubao_asr;
    saveButton.disabled = busy || !detail.capabilities.save_video_text;
    actions.append(videoButton, doubaoButton, saveButton);
    panel.append(text, source, actions);
    return panel;
  }

  private renderComments(detail: ModelMrWorkDetail): HTMLElement {
    const panel = document.createElement('div');
    panel.className = 'model-comments-panel';
    const active = this.commentTabs.get(detail.work.id) || 'author';
    const threads = this.commentThreads(detail.comments);
    const authorThreads = threads.filter((thread) => this.threadHasAuthorInteraction(thread));
    const rankedThreads = threads
      .filter((thread) => {
        const lead = thread.root || thread.replies[0];
        return lead && !this.isAuthorComment(lead) && !this.isLowValueComment(lead.text);
      })
      .sort((left, right) => this.compareCommentThreads(left, right));
    const tabs = document.createElement('nav');
    tabs.className = 'model-comment-tabs';
    const definitions: Array<{ key: CommentTab; label: string; count: number }> = [
      { key: 'author', label: '本人回复', count: authorThreads.length },
      { key: 'ranking', label: '评论排行', count: rankedThreads.length },
      { key: 'stocks', label: '评股', count: detail.stock_mentions?.items?.length || 0 },
    ];
    definitions.forEach((definition) => {
      const button = this.actionButton(`${definition.label} ${definition.count}`, detail.work.id, 'comment-tab');
      button.dataset.commentTab = definition.key;
      button.classList.toggle('is-active', definition.key === active);
      tabs.append(button);
    });
    panel.append(tabs);

    const limit = this.commentLimits.get(detail.work.id) || 60;
    let remaining = 0;
    if (active === 'author') {
      const note = document.createElement('p');
      note.className = 'model-comment-sort-note';
      note.textContent = '只显示模型先生本人回复或本人点赞过的评论，并保留对应提问上下文。';
      panel.append(note);
      authorThreads.slice(0, limit).forEach((thread) => panel.append(this.renderCommentThread(thread, true)));
      if (!authorThreads.length) panel.append(this.message('这条作品暂未识别到模型先生本人回复。'));
      remaining = Math.max(0, authorThreads.length - limit);
    } else if (active === 'ranking') {
      const note = document.createElement('p');
      note.className = 'model-comment-sort-note';
      note.textContent = '按点赞量优先，其次按回复数和有效正文长度排列；纯表情、纯“哈哈”等低价值评论不进入排行。';
      panel.append(note);
      rankedThreads.slice(0, limit).forEach((thread, index) => panel.append(this.renderCommentThread(thread, false, index + 1)));
      if (!rankedThreads.length) panel.append(this.message('这条作品当前没有可参与排行的评论。'));
      remaining = Math.max(0, rankedThreads.length - limit);
    } else {
      panel.append(this.renderStockMentions(detail));
    }
    if (remaining) {
      const more = this.actionButton(`继续显示（还有 ${remaining} 组）`, detail.work.id, 'more-comments');
      more.classList.add('model-comments-more');
      panel.append(more);
    }
    const note = document.createElement('p');
    note.className = 'model-comments-note';
    note.textContent = `已同步 ${detail.comments.length} 条；原始评论媒体、账号主页及来源编号未带入云端。`;
    panel.append(note);
    return panel;
  }

  private commentThreads(comments: ModelMrComment[]): ModelMrCommentThread[] {
    const threads = new Map<string, ModelMrCommentThread>();
    comments.forEach((comment) => {
      const key = comment.thread_key || `comment-${comment.id}`;
      let thread = threads.get(key);
      if (!thread) {
        thread = { key, root: null, replies: [] };
        threads.set(key, thread);
      }
      if (!comment.reply_depth && !thread.root) thread.root = comment;
      else thread.replies.push(comment);
    });
    return Array.from(threads.values());
  }

  private threadHasAuthorInteraction(thread: ModelMrCommentThread): boolean {
    return [thread.root, ...thread.replies].some((comment) => comment && (this.isAuthorComment(comment) || comment.author_liked));
  }

  private isAuthorComment(comment: ModelMrComment): boolean {
    return comment.kind.includes('author') || comment.author.trim() === '模型先生';
  }

  private isLowValueComment(text: string): boolean {
    const compact = text
      .replace(/\[[^\]]{1,12}\]/g, '')
      .replace(/@\S+/g, '')
      .replace(/[^A-Za-z0-9\u4e00-\u9fff]+/g, '')
      .toLowerCase();
    if (!compact) return true;
    if (/^(哈|呵|嘿|嘻){1,12}$/.test(compact) || /^6{1,12}$/.test(compact)) return true;
    return new Set(['嗯', '哦', '啊', '好', '赞', '点赞', '支持', '收到', '路过', '来了', '呵呵', '谢谢', '感谢', '学习了', '关注了', '蹲', '蹲一个']).has(compact);
  }

  private compareCommentThreads(left: ModelMrCommentThread, right: ModelMrCommentThread): number {
    const leftLead = left.root || left.replies[0];
    const rightLead = right.root || right.replies[0];
    const likes = (rightLead?.like_count || 0) - (leftLead?.like_count || 0);
    if (likes) return likes;
    const replies = (rightLead?.reply_count || right.replies.length) - (leftLead?.reply_count || left.replies.length);
    if (replies) return replies;
    return (rightLead?.text.length || 0) - (leftLead?.text.length || 0);
  }

  private renderCommentThread(thread: ModelMrCommentThread, authorMode: boolean, rank = 0): HTMLElement {
    const section = document.createElement('section');
    section.className = `model-comment-thread${authorMode ? ' is-author-thread' : ''}`;
    if (rank) {
      const badge = document.createElement('span');
      badge.className = 'model-comment-rank';
      badge.textContent = String(rank);
      section.append(badge);
    }
    if (thread.root) section.append(this.renderComment(thread.root));
    const replies = [...thread.replies].sort((left, right) => {
      const authorDifference = Number(this.isAuthorComment(right)) - Number(this.isAuthorComment(left));
      return authorDifference || right.like_count - left.like_count;
    });
    const visibleReplies = authorMode
      ? replies.filter((comment) => this.isAuthorComment(comment) || comment.author_liked || !this.isLowValueComment(comment.text))
      : replies.slice(0, 6);
    visibleReplies.forEach((comment) => section.append(this.renderComment(comment)));
    return section;
  }

  private renderStockMentions(detail: ModelMrWorkDetail): HTMLElement {
    const report = detail.stock_mentions;
    const root = document.createElement('section');
    root.className = 'model-stock-report';
    if (!report?.items?.length) {
      root.append(this.message('这条作品暂时没有评股排行；重新同步主人资料后会使用原智能体的本地证券名称表生成。'));
      return root;
    }
    const heading = document.createElement('header');
    const title = document.createElement('b');
    title.textContent = '评论区股票热度';
    const summary = document.createElement('span');
    summary.textContent = `已检查 ${report.total_comments} 条评论 · ${report.items.length} 只股票`;
    heading.append(title, summary);
    root.append(heading);
    const commentsById = new Map(detail.comments.map((comment) => [comment.id, comment]));
    report.items.forEach((item) => {
      const row = document.createElement('details');
      row.className = 'model-stock-row';
      const rowSummary = document.createElement('summary');
      const rank = document.createElement('span');
      rank.className = 'model-stock-rank';
      rank.textContent = String(item.rank);
      const name = document.createElement('b');
      name.textContent = item.code ? `${item.name} · ${item.code}` : item.name;
      const count = document.createElement('span');
      count.textContent = `${item.comment_count} 条 · 粉丝 ${item.fan_comment_count} · 本人 ${item.author_comment_count}`;
      rowSummary.append(rank, name, count);
      row.append(rowSummary);
      const matched = item.comment_ids.map((id) => commentsById.get(id)).filter((comment): comment is ModelMrComment => Boolean(comment));
      if (matched.length) matched.forEach((comment) => row.append(this.renderComment(comment)));
      else item.examples.forEach((example) => {
        const text = document.createElement('p');
        text.className = 'model-stock-example';
        text.textContent = example;
        row.append(text);
      });
      root.append(row);
    });
    const footer = document.createElement('p');
    footer.className = 'model-comments-note';
    footer.textContent = report.message || '使用原智能体本地证券名称表生成，不调用 AI，不产生 API 费用。';
    root.append(footer);
    return root;
  }

  private renderComment(comment: ModelMrComment): HTMLElement {
    const item = document.createElement('article');
    item.className = `model-comment${comment.reply_depth ? ' is-reply' : ''}${comment.kind.includes('author') ? ' is-author' : ''}`;
    const header = document.createElement('header');
    const author = document.createElement('b');
    author.textContent = comment.kind.includes('author') ? `${comment.author} · 作者` : comment.author;
    const date = document.createElement('time');
    date.textContent = this.formatDate(comment.published_at);
    header.append(author, date);
    const text = document.createElement('p');
    text.textContent = comment.text;
    const metrics = document.createElement('small');
    metrics.textContent = `赞 ${comment.like_count}${comment.reply_count ? ` · 回复 ${comment.reply_count}` : ''}${comment.author_liked ? ' · 本人点赞' : ''}`;
    item.append(header, text, metrics);
    return item;
  }

  private showMoreComments(workId: number): void {
    this.commentLimits.set(workId, (this.commentLimits.get(workId) || 60) + 60);
    this.renderWorks();
  }

  private editTitle(workId: number): void {
    if (this.busyWorks.has(workId)) return;
    this.editingTitles.add(workId);
    this.workMessages.delete(workId);
    this.renderWorks();
    requestAnimationFrame(() => {
      const input = this.element.querySelector<HTMLInputElement>(`#model-title-${workId}`);
      input?.focus();
      input?.select();
    });
  }

  private cancelTitle(workId: number): void {
    this.editingTitles.delete(workId);
    this.workMessages.delete(workId);
    this.renderWorks();
  }

  private async saveTitle(workId: number): Promise<void> {
    if (this.busyWorks.has(workId)) return;
    const input = this.element.querySelector<HTMLInputElement>(`#model-title-${workId}`);
    const title = input?.value.trim() || '';
    if (!title) {
      this.setWorkMessage(workId, '作品标题不能为空。', 'is-error');
      this.renderWorks();
      return;
    }
    this.busyWorks.add(workId);
    this.setWorkMessage(workId, '正在保存标题…', '');
    this.renderWorks();
    try {
      const result = await instantApi.saveModelMrTitle(workId, title);
      const work = this.works.find((item) => item.id === workId);
      if (work) work.title = result.title;
      const detail = this.details.get(workId);
      if (detail) detail.work.title = result.title;
      this.editingTitles.delete(workId);
      this.setWorkMessage(workId, '标题已保存。', 'is-done');
    } catch (error) {
      this.setWorkMessage(workId, error instanceof Error ? error.message : '标题保存失败。', 'is-error');
    } finally {
      this.busyWorks.delete(workId);
      this.renderWorks();
    }
  }

  private setCommentTab(workId: number, tab: CommentTab): void {
    this.commentTabs.set(workId, tab);
    this.commentLimits.set(workId, 60);
    this.renderWorks();
  }

  private async transcribe(workId: number, engine: 'video' | 'doubao'): Promise<void> {
    if (this.busyWorks.has(workId)) return;
    if (engine === 'doubao' && !window.confirm('豆包识别会提取本地视频音频并按音频时长调用付费接口。确认继续吗？')) return;
    this.busyWorks.add(workId);
    this.setWorkMessage(workId, engine === 'doubao' ? '正在读取豆包识别结果…' : '正在识别视频文字…', '');
    this.renderWorks();
    try {
      const result = await instantApi.transcribeModelMrWork(workId, engine);
      const detail = this.details.get(workId);
      if (detail) detail.video_text.text = result.text;
      this.setWorkMessage(workId, result.message, result.cached ? 'is-cached' : 'is-done');
    } catch (error) {
      this.setWorkMessage(workId, error instanceof Error ? error.message : '识别失败。', 'is-error');
    } finally {
      this.busyWorks.delete(workId);
      this.renderWorks();
    }
  }

  private async saveVideoText(workId: number): Promise<void> {
    if (this.busyWorks.has(workId)) return;
    const textarea = this.element.querySelector<HTMLTextAreaElement>(`#model-video-text-${workId}`);
    const text = textarea?.value.trim() || '';
    if (!text) {
      this.setWorkMessage(workId, '视频原文不能为空。', 'is-error');
      this.renderWorks();
      return;
    }
    this.busyWorks.add(workId);
    this.setWorkMessage(workId, '正在保存正式原文…', '');
    try {
      const result = await instantApi.saveModelMrVideoText(workId, text);
      const detail = this.details.get(workId);
      if (detail) detail.video_text = { ...detail.video_text, text: result.text, official: true, source: result.mode };
      this.setWorkMessage(workId, '正式原文已保存。', 'is-done');
    } catch (error) {
      this.setWorkMessage(workId, error instanceof Error ? error.message : '保存失败。', 'is-error');
    } finally {
      this.busyWorks.delete(workId);
      this.renderWorks();
    }
  }

  private setWorkMessage(workId: number, text: string, tone: string): void {
    this.workMessages.set(workId, { text, tone });
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
    text.textContent = message || '请确认模型先生主人资料库已经同步。';
    const boundary = document.createElement('small');
    boundary.textContent = '仅显示主人的作品视频、正式文字、评论和投资思路；粉丝资料、管理功能、密钥及原始数据库不会进入即时 AI。';
    box.append(mark, title, text, boundary);
    this.body.replaceChildren(box);
  }

  private actionButton(text: string, workId: number, action: string, detailTab?: string, primary = false): HTMLButtonElement {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.modelAction = action;
    button.dataset.workId = String(workId);
    if (detailTab) button.dataset.detailTab = detailTab;
    if (primary) button.classList.add('is-primary');
    button.textContent = text;
    return button;
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
