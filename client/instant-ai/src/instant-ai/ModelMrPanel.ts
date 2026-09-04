import { instantApi } from './api';
import { commentThreads, isAuthorComment, rankCommentThreads, threadHasAuthorInteraction, type ModelMrCommentThread } from './ModelMrComments';
import type {
  ModelMrChatConfig, ModelMrComment, ModelMrThoughtCategory, ModelMrWork, ModelMrWorkDetail,
} from './types';

type ModelMrTab = 'works' | 'thoughts' | 'chat';
type WorkDetailTab = 'video' | 'text' | 'comments' | 'keywords' | 'interpretation';
type CommentTab = 'author' | 'ranking' | 'stocks';
const MODEL_MR_WORK_PAGE_SIZE = 24;

export class ModelMrPanel {
  public readonly element: HTMLElement;
  private readonly body: HTMLElement;
  private readonly badge: HTMLElement;
  private activeTab: ModelMrTab = 'works';
  private works: ModelMrWork[] = [];
  private totalWorks = 0;
  private nextWorksOffset = 0;
  private hasMoreWorks = false;
  private loadingMoreWorks = false;
  private worksLoadMessage = '';
  private worksObserver: IntersectionObserver | null = null;
  private thoughts: ModelMrThoughtCategory[] = [];
  private selectedThought: number | null = null;
  private relatedWorks: ModelMrWork[] = [];
  private relatedTotal = 0;
  private relatedOffset = 0;
  private relatedHasMore = false;
  private relatedLoading = false;
  private relatedMessage = '';
  private relatedQuery = '';
  private relatedKeywords: string[] = [];
  private relatedRequest = 0;
  private readonly editingKeywords = new Set<number>();
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
      if ((event.target as HTMLElement).id === 'modelMrThoughtSearch') {
        event.preventDefault();
        this.relatedQuery = this.element.querySelector<HTMLInputElement>('#modelMrThoughtQuery')?.value.trim() || '';
        if (this.selectedThought !== null) void this.openThought(this.selectedThought, true);
        return;
      }
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
      instantApi.modelMrWorks(MODEL_MR_WORK_PAGE_SIZE, 0), instantApi.modelMrThoughts(), instantApi.modelMrChatConfig(),
    ]);
    if (this.works.length) {
      const newestIds = new Set(works.items.map((work) => work.id));
      this.works = [...works.items, ...this.works.filter((work) => !newestIds.has(work.id))];
      this.nextWorksOffset = Math.max(this.nextWorksOffset, works.offset + works.count);
    } else {
      this.works = works.items;
      this.nextWorksOffset = works.offset + works.count;
    }
    this.totalWorks = Math.max(status.counts?.works ?? 0, works.total, this.works.length);
    this.hasMoreWorks = this.nextWorksOffset < this.totalWorks || works.has_more;
    this.worksLoadMessage = '';
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
    if (action.dataset.modelAction === 'load-more') {
      await this.loadMoreWorks();
      return;
    }
    if (action.dataset.modelAction === 'thought-open') {
      await this.openThought(Number(action.dataset.categoryId));
      return;
    }
    if (action.dataset.modelAction === 'thought-back') {
      const parent = this.thoughts.find((item) => item.id === this.selectedThought)?.parent_id;
      if (parent) await this.openThought(parent);
      else {
        this.selectedThought = null;
        this.relatedRequest++;
        this.relatedLoading = false;
        this.renderThoughts();
      }
      return;
    }
    if (action.dataset.modelAction === 'thought-more') {
      await this.loadThoughtPage();
      return;
    }
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
    else if (command === 'edit-keywords') { this.editingKeywords.add(workId); this.renderWorks(); }
    else if (command === 'cancel-keywords') { this.editingKeywords.delete(workId); this.renderWorks(); }
    else if (command === 'save-keywords') await this.saveKeywords(workId);
    else if (command === 'comment-tab') this.setCommentTab(workId, (action.dataset.commentTab || 'author') as CommentTab);
  }

  private selectTab(tab: ModelMrTab): void {
    this.worksObserver?.disconnect();
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
    if (this.activeTab !== 'works') {
      if (this.activeTab === 'thoughts') this.renderThoughts();
      return;
    }
    this.worksObserver?.disconnect();
    this.worksObserver = null;
    const list = document.createElement('div');
    list.className = 'model-work-list model-work-list-full';
    this.works.forEach((work) => list.append(this.renderWorkCard(work)));
    if (!this.works.length) list.append(this.message('模型先生作品库当前没有可显示内容。'));
    else list.append(this.renderWorksLoader());
    this.body.replaceChildren(list);
    this.observeWorksLoader();
  }

  private renderWorksLoader(): HTMLElement {
    const loader = document.createElement('footer');
    loader.className = `model-work-loader${this.loadingMoreWorks ? ' is-loading' : ''}`;
    loader.dataset.modelLoadTrigger = 'true';
    loader.setAttribute('role', 'status');
    const status = document.createElement('p');
    if (this.worksLoadMessage) status.textContent = this.worksLoadMessage;
    else if (this.loadingMoreWorks) status.textContent = '正在加载更早的作品…';
    else if (this.hasMoreWorks) status.textContent = `已显示 ${this.works.length} / ${this.totalWorks} 部，继续向下滑自动加载`;
    else status.textContent = `已显示全部 ${this.works.length} 部作品`;
    loader.append(status);
    if (this.hasMoreWorks) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.modelAction = 'load-more';
      button.textContent = this.loadingMoreWorks ? '加载中…' : '加载更多作品';
      button.disabled = this.loadingMoreWorks;
      loader.append(button);
    }
    return loader;
  }

  private observeWorksLoader(): void {
    if (!this.hasMoreWorks || this.loadingMoreWorks || !('IntersectionObserver' in window)) return;
    const trigger = this.element.querySelector<HTMLElement>('[data-model-load-trigger]');
    if (!trigger) return;
    this.worksObserver = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) void this.loadMoreWorks();
    }, { root: null, rootMargin: '240px 0px', threshold: 0 });
    this.worksObserver.observe(trigger);
  }

  private async loadMoreWorks(): Promise<void> {
    if (this.activeTab !== 'works' || this.loadingMoreWorks || !this.hasMoreWorks) return;
    this.loadingMoreWorks = true;
    this.worksLoadMessage = '';
    this.renderWorks();
    try {
      const page = await instantApi.modelMrWorks(MODEL_MR_WORK_PAGE_SIZE, this.nextWorksOffset);
      const loadedIds = new Set(this.works.map((work) => work.id));
      page.items.forEach((work) => {
        if (!loadedIds.has(work.id)) this.works.push(work);
      });
      this.nextWorksOffset = page.offset + page.count;
      this.totalWorks = Math.max(this.totalWorks, page.total, this.works.length);
      this.hasMoreWorks = page.count > 0 && (page.has_more || this.nextWorksOffset < this.totalWorks);
      if (!page.count) this.worksLoadMessage = '没有读取到更多作品，请稍后再试。';
    } catch (error) {
      this.worksLoadMessage = error instanceof Error ? `加载失败：${error.message}` : '加载失败，请稍后重试。';
    } finally {
      this.loadingMoreWorks = false;
      this.renderWorks();
    }
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
    work.keywords.slice(0, 8).forEach((keyword) => meta.append(this.pill(keyword)));
    if (work.keywords.length > 8) meta.append(this.pill(`共 ${work.keywords.length} 个关键词`));
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
    actions.append(this.actionButton(`AI关键词 ${work.keywords.length}`, work.id, 'open-detail', 'keywords'));
    if (work.has_interpretation) actions.append(this.actionButton('解读感悟', work.id, 'open-detail', 'interpretation'));
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
    (['video', 'text', 'comments', 'keywords', 'interpretation'] as WorkDetailTab[]).forEach((value) => {
      const labels: Record<WorkDetailTab, string> = { video: '本地视频', text: '视频原文', comments: `评论 ${detail.comment_total}`, keywords: 'AI关键词', interpretation: '解读感悟' };
      const button = this.actionButton(labels[value], workId, 'detail-tab', value);
      button.classList.toggle('is-active', value === tab);
      tabs.append(button);
    });
    tabs.append(this.actionButton('收起', workId, 'close-detail'));
    const content = document.createElement('div');
    content.className = 'model-detail-content';
    if (tab === 'video') content.append(this.renderVideo(detail));
    else if (tab === 'text') content.append(this.renderVideoText(detail));
    else if (tab === 'keywords') content.append(this.renderKeywords(detail));
    else if (tab === 'interpretation') {
      const interpretation = document.createElement('p');
      interpretation.className = 'model-saved-interpretation';
      interpretation.textContent = detail.interpretation.text || '尚未保存解读感悟。本页不会自动调用 AI。';
      content.append(interpretation);
    }
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
    const threads = commentThreads(detail.comments);
    const authorThreads = threads.filter(threadHasAuthorInteraction);
    const ranked = rankCommentThreads(threads);
    const tabs = document.createElement('nav');
    tabs.className = 'model-comment-tabs';
    const definitions: Array<{ key: CommentTab; label: string; count: number }> = [
      { key: 'author', label: '作者互动', count: authorThreads.length },
      { key: 'ranking', label: '粉丝评论', count: ranked.topLiked.length + ranked.remaining.length },
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
      note.textContent = '红色“作者”标识表示本人发言；“作者赞过”表示作者点过赞。保留原提问和同楼上下文，作者身份以已采集标记为准。';
      panel.append(note);
      authorThreads.slice(0, limit).forEach((thread) => panel.append(this.renderCommentThread(thread, true)));
      if (!authorThreads.length) panel.append(this.message('这条作品暂未识别到模型先生本人回复。'));
      remaining = Math.max(0, authorThreads.length - limit);
    } else if (active === 'ranking') {
      const note = document.createElement('p');
      note.className = 'model-comment-sort-note';
      note.textContent = '高赞前十：按点赞数、回复数排序。其余：20 字以上有效文字优先，再按有效字数（最多计 500 字）、回复数、点赞数排序。排除纯表情和灌水；这是阅读排序，不代表观点正确。';
      panel.append(note);
      const high = document.createElement('section');
      high.className = 'model-comment-group model-high-liked';
      const highHeading = document.createElement('h4');
      highHeading.textContent = `高赞前十 · ${ranked.topLiked.length} 组`;
      high.append(highHeading);
      ranked.topLiked.forEach((thread, index) => high.append(this.renderCommentThread(thread, false, index + 1)));
      if (!ranked.topLiked.length) high.append(this.message('暂无有点赞的有效评论。'));
      const rest = document.createElement('section');
      rest.className = 'model-comment-group model-quality-comments';
      const restHeading = document.createElement('h4');
      restHeading.textContent = `其余评论 · 有效长回复优先（${ranked.remaining.length} 组）`;
      rest.append(restHeading);
      ranked.remaining.slice(0, limit).forEach(thread => rest.append(this.renderCommentThread(thread, false)));
      panel.append(high, rest);
      if (!ranked.topLiked.length && !ranked.remaining.length) panel.append(this.message('这条作品当前没有可参与排行的评论。'));
      remaining = Math.max(0, ranked.remaining.length - limit);
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

  private renderCommentThread(thread: ModelMrCommentThread, authorMode: boolean, rank = 0): HTMLElement {
    const section = document.createElement('section');
    section.className = `model-comment-thread${authorMode ? ' is-author-thread' : ''}`;
    section.dataset.threadKey = thread.key;
    if (rank) {
      const badge = document.createElement('span');
      badge.className = 'model-comment-rank';
      badge.textContent = String(rank);
      section.append(badge);
    }
    if (thread.root) section.append(this.renderComment(thread.root));
    const replies = [...thread.replies].sort((left, right) => {
      const authorDifference = Number(isAuthorComment(right)) - Number(isAuthorComment(left));
      return authorDifference || Number(right.author_liked) - Number(left.author_liked) || right.like_count - left.like_count;
    });
    replies.slice(0, 6).forEach(comment => section.append(this.renderComment(comment)));
    if (replies.length > 6) {
      const more = document.createElement('details');
      more.className = 'model-thread-more';
      const summary = document.createElement('summary');
      summary.textContent = `展开同楼其余回复（${replies.length - 6} 条）`;
      more.append(summary);
      let loaded = 6;
      const load = document.createElement('button');
      load.type = 'button';
      load.textContent = '继续显示同楼回复';
      const appendPage = () => {
        replies.slice(loaded, loaded + 30).forEach(comment => more.insertBefore(this.renderComment(comment), load));
        loaded += 30;
        load.hidden = loaded >= replies.length;
      };
      load.addEventListener('click', appendPage);
      more.append(load);
      more.addEventListener('toggle', () => { if (more.open && loaded === 6) appendPage(); });
      section.append(more);
    }
    return section;
  }

  private renderStockMentions(detail: ModelMrWorkDetail): HTMLElement {
    const report = detail.stock_mentions;
    const root = document.createElement('section');
    root.className = 'model-stock-report';
    if (!report || (!report.method && !report.items.length)) {
      root.append(this.message('此作品尚无已同步的评股报告，不能据此判断评论中没有股票。这里不会自动采集或调用 AI。'));
      return root;
    }
    const items = [...report.items].sort((a, b) => b.comment_count - a.comment_count || b.mention_count - a.mention_count || a.code.localeCompare(b.code)).slice(0, 20);
    const heading = document.createElement('header');
    const title = document.createElement('b');
    title.textContent = '评论区股票热度';
    const summary = document.createElement('span');
    summary.textContent = `报告已检查 ${report.total_comments} 条评论 · 展示 ${items.length} / ${report.stock_count} 只股票`;
    heading.append(title, summary);
    root.append(heading);
    const explanation = document.createElement('p');
    explanation.className = 'model-comment-sort-note';
    explanation.textContent = '按提及股票的评论条数排序，最多展示前 20 只；同条评论重复提及不重复计数。点击可看相关评论。热度不代表作者推荐、持仓或投资建议。';
    root.append(explanation);
    const threads = commentThreads(detail.comments);
    if (!items.length) root.append(this.message('已有报告中没有可唯一识别的股票提及。'));
    items.forEach((item, index) => {
      const row = document.createElement('details');
      row.className = `model-stock-row${index < 3 ? ' is-top-stock' : ''}`;
      const rowSummary = document.createElement('summary');
      const rank = document.createElement('span');
      rank.className = 'model-stock-rank';
      rank.textContent = String(index + 1);
      const identity = document.createElement('div');
      identity.className = 'model-stock-identity';
      const name = document.createElement('b');
      name.textContent = item.name;
      const code = document.createElement('small');
      code.textContent = item.code;
      const breakdown = document.createElement('small');
      breakdown.className = 'model-stock-breakdown';
      breakdown.textContent = `粉丝 ${item.fan_comment_count} · 作者 ${item.author_comment_count}`;
      identity.append(name, code, breakdown);
      const count = document.createElement('span');
      count.className = 'model-stock-count';
      count.textContent = `${item.comment_count} 条评论`;
      rowSummary.append(rank, identity, count);
      row.append(rowSummary);
      let expanded = false;
      row.addEventListener('toggle', () => {
        if (!row.open || expanded) return;
        expanded = true;
        const ids = new Set(item.comment_ids);
        const matched = threads.filter(thread => [thread.root, ...thread.replies].some(comment => comment && ids.has(comment.id)));
        const available = new Set(detail.comments.filter(comment => ids.has(comment.id)).map(comment => comment.id)).size;
        const note = document.createElement('p');
        note.className = 'model-stock-example';
        note.textContent = `报告 ${item.comment_count} 条提及评论；当前已同步关联正文 ${available} 条。上下文可能包含未提及此股票的提问/回复，不计入热度。`;
        row.append(note);
        if (matched.length) {
          let offset = 0;
          const more = document.createElement('button');
          more.type = 'button';
          more.className = 'model-comments-more';
          more.textContent = '继续显示相关评论';
          row.append(more);
          const appendPage = () => {
            matched.slice(offset, offset + 20).forEach(thread => row.insertBefore(this.renderCommentThread(thread, false), more));
            offset += 20;
            more.hidden = offset >= matched.length;
          };
          more.addEventListener('click', appendPage);
          appendPage();
        } else {
          if (!item.examples.length) row.append(this.message('关联正文尚未同步，请勿把缺少正文当成零提及。'));
          item.examples.forEach(example => {
            const text = document.createElement('p');
            text.className = 'model-stock-example';
            text.textContent = `已保存的报告摘录：${example}`;
            row.append(text);
          });
        }
      });
      root.append(row);
    });
    if (report.uncertain.length) {
      const uncertain = document.createElement('details');
      uncertain.className = 'model-stock-uncertain';
      const title = document.createElement('summary');
      title.textContent = `待核对简称 ${report.uncertain.length} 项（不计入股票排名）`;
      uncertain.append(title);
      report.uncertain.forEach(item => {
        const line = document.createElement('p');
        line.textContent = `${item.text} · ${item.comment_count} 条${item.candidates.length ? ` · 候选：${item.candidates.join('、')}` : ''}`;
        uncertain.append(line);
      });
      root.append(uncertain);
    }
    const footer = document.createElement('p');
    footer.className = 'model-comments-note';
    footer.textContent = report.message || '使用原智能体本地证券名称表生成，不调用 AI，不产生 API 费用。';
    root.append(footer);
    return root;
  }

  private renderComment(comment: ModelMrComment): HTMLElement {
    const item = document.createElement('article');
    item.className = `model-comment${comment.reply_depth ? ' is-reply' : ''}${isAuthorComment(comment) ? ' is-author' : ''}${comment.author_liked ? ' is-author-liked' : ''}`;
    item.dataset.commentId = String(comment.id);
    const header = document.createElement('header');
    const author = document.createElement('b');
    author.textContent = comment.author;
    const identity = document.createElement('div');
    identity.className = 'model-comment-identity';
    identity.append(author);
    if (isAuthorComment(comment)) {
      const badge = document.createElement('span');
      badge.className = 'model-author-badge';
      badge.textContent = '作者';
      identity.append(badge);
    }
    const date = document.createElement('time');
    date.textContent = this.formatDate(comment.published_at);
    header.append(identity, date);
    const text = document.createElement('p');
    text.textContent = comment.text;
    const metrics = document.createElement('small');
    metrics.textContent = `赞 ${comment.like_count}${comment.reply_count ? ` · 回复 ${comment.reply_count}` : ''}`;
    item.append(header, text, metrics);
    if (comment.author_liked) {
      const liked = document.createElement('span');
      liked.className = 'model-author-liked-badge';
      liked.textContent = '♥ 作者赞过';
      item.append(liked);
    }
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
      [...this.works, ...this.relatedWorks].filter((item) => item.id === workId).forEach((work) => { work.title = result.title; });
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
    this.worksObserver?.disconnect();
    const root = document.createElement('div');
    root.className = 'model-thought-list';
    const selected = this.thoughts.find((item) => item.id === this.selectedThought);
    if (selected) {
      root.classList.add('model-thought-detail');
      const back = document.createElement('button');
      back.type = 'button';
      back.dataset.modelAction = 'thought-back';
      back.textContent = selected.parent_id ? '‹ 返回一级分类' : '‹ 全部投资思路';
      const heading = document.createElement('h3');
      const parent = this.thoughts.find((item) => item.id === selected.parent_id);
      heading.textContent = `${parent ? `${parent.name} › ` : ''}${selected.name}`;
      const description = document.createElement('p');
      description.textContent = selected.description;
      const children = document.createElement('div');
      children.className = 'model-thought-children';
      this.thoughts.filter((item) => item.parent_id === selected.id).forEach((item) => children.append(this.thoughtButton(item)));
      const search = document.createElement('form');
      search.id = 'modelMrThoughtSearch';
      search.className = 'model-thought-search';
      const input = document.createElement('input');
      input.id = 'modelMrThoughtQuery';
      input.maxLength = 120;
      input.value = this.relatedQuery;
      input.placeholder = '搜索本分类的标题、行业或关键词';
      input.setAttribute('aria-label', input.placeholder);
      const submit = document.createElement('button');
      submit.type = 'submit';
      submit.textContent = '搜索';
      search.append(input, submit);
      const tags = document.createElement('div');
      tags.className = 'model-work-meta';
      this.relatedKeywords.forEach((keyword) => tags.append(this.pill(keyword)));
      const list = document.createElement('div');
      list.className = 'model-work-list model-work-list-full';
      this.relatedWorks.forEach((work) => list.append(this.renderWorkCard(work)));
      const status = document.createElement('p');
      status.setAttribute('role', 'status');
      status.textContent = this.relatedLoading ? '正在加载相关作品…' : this.relatedMessage || `相关作品：已显示 ${this.relatedWorks.length} / ${this.relatedTotal} 部`;
      root.append(back, heading, description, children, search, tags, status, list);
      if (!this.relatedLoading && !this.relatedWorks.length && !this.relatedMessage) root.append(this.message('本分类暂无匹配作品，可清空搜索词后重试。'));
      if (this.relatedHasMore || this.relatedMessage) {
        const more = document.createElement('button');
        more.type = 'button';
        more.dataset.modelAction = 'thought-more';
        more.disabled = this.relatedLoading;
        more.textContent = this.relatedLoading ? '加载中…' : this.relatedMessage ? '重试加载' : '加载更多相关作品';
        root.append(more);
      }
      this.body.replaceChildren(root);
      return;
    }
    const parents = this.thoughts.filter((category) => category.level === 1);
    parents.forEach((parent) => {
      const section = document.createElement('section');
      const heading = document.createElement('header');
      heading.append(this.thoughtButton(parent));
      if (parent.description) {
        const description = document.createElement('p');
        description.textContent = parent.description;
        heading.append(description);
      }
      const children = document.createElement('div');
      children.className = 'model-thought-children';
      this.thoughts.filter((item) => item.parent_id === parent.id).forEach((child) => {
        children.append(this.thoughtButton(child));
      });
      section.append(heading, children);
      root.append(section);
    });
    if (!parents.length) root.append(this.message('投资思路索引当前没有可显示内容。'));
    this.body.replaceChildren(root);
  }

  private thoughtButton(category: ModelMrThoughtCategory): HTMLButtonElement {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.modelAction = 'thought-open';
    button.dataset.categoryId = String(category.id);
    button.textContent = `${category.name} · ${category.video_count} 部 ›`;
    return button;
  }

  private async openThought(id: number, keepQuery = false): Promise<void> {
    if (!this.thoughts.some((item) => item.id === id)) return;
    this.selectedThought = id;
    this.relatedRequest++;
    this.relatedLoading = false;
    this.relatedWorks = [];
    this.relatedTotal = 0;
    this.relatedOffset = 0;
    this.relatedKeywords = [];
    this.relatedHasMore = true;
    this.relatedMessage = '';
    if (!keepQuery) this.relatedQuery = '';
    await this.loadThoughtPage();
  }

  private async loadThoughtPage(): Promise<void> {
    if (this.selectedThought === null || this.relatedLoading) return;
    const generation = ++this.relatedRequest;
    const id = this.selectedThought;
    this.relatedLoading = true;
    this.relatedMessage = '';
    this.renderThoughts();
    try {
      const page = await instantApi.modelMrThoughtWorks(id, MODEL_MR_WORK_PAGE_SIZE, this.relatedOffset, this.relatedQuery);
      if (generation !== this.relatedRequest) return;
      const ids = new Set(this.relatedWorks.map((item) => item.id));
      this.relatedWorks.push(...page.items.filter((item) => !ids.has(item.id)));
      this.relatedOffset = page.offset + page.count;
      this.relatedTotal = page.total;
      this.relatedHasMore = page.has_more && page.count > 0;
      this.relatedKeywords = page.keywords;
      this.relatedMessage = page.message || '';
    } catch (error) {
      if (generation === this.relatedRequest) this.relatedMessage = error instanceof Error ? error.message : '分类加载失败。';
    } finally {
      if (generation === this.relatedRequest) {
        this.relatedLoading = false;
        if (this.activeTab === 'thoughts') this.renderThoughts();
      }
    }
  }

  private renderKeywords(detail: ModelMrWorkDetail): HTMLElement {
    const panel = document.createElement('div');
    panel.className = 'model-keyword-panel';
    const info = detail.work.keyword_info;
    const note = document.createElement('p');
    note.textContent = info?.edited_by_owner ? '已保存的主人整理结果；不会自动重提炼。' : '显示本地已保存的 AI 提炼结果；查看和手动整理不调用 AI。';
    panel.append(note);
    if (info?.stale) panel.append(this.message('原文可能已变化，请核对已有关键词；本页不会自动产生新的提炼结果。'));
    const editing = this.editingKeywords.has(detail.work.id);
    const groups = Object.entries(info?.categories || {});
    const categorized = new Set(groups.flatMap(([, words]) => words));
    const extra = detail.work.keywords.filter((word) => !categorized.has(word));
    [...groups, ['其他关键词', extra] as [string, string[]]].forEach(([name, words]) => {
      if (!words.length && !editing) return;
      const group = document.createElement('section');
      const title = document.createElement('h4');
      title.textContent = name;
      group.append(title);
      if (editing) {
        const input = document.createElement('textarea');
        input.dataset.keywordCategory = name;
        input.setAttribute('aria-label', name);
        input.value = words.join('、');
        input.maxLength = name === '其他关键词' ? 5000 : 600;
        group.append(input);
      } else words.forEach((word) => group.append(this.pill(word)));
      panel.append(group);
    });
    if (!detail.work.keywords.length && !editing) panel.append(this.message('此作品尚无已保存关键词，不会自动调用付费提炼。'));
    if (editing) {
      panel.append(this.message('用顿号、逗号或换行分隔；每类最多 8 个关键词。'));
      const save = this.actionButton('保存关键词', detail.work.id, 'save-keywords', undefined, true);
      save.disabled = this.busyWorks.has(detail.work.id);
      panel.append(save, this.actionButton('取消整理', detail.work.id, 'cancel-keywords'));
    } else if (detail.work.keyword_revision) panel.append(this.actionButton('手动整理关键词', detail.work.id, 'edit-keywords'));
    return panel;
  }

  private async saveKeywords(workId: number): Promise<void> {
    const detail = this.details.get(workId);
    if (!detail || this.busyWorks.has(workId)) return;
    const categories: Record<string, string[]> = {};
    let extra: string[] = [];
    this.element.querySelectorAll<HTMLTextAreaElement>(`[data-work-id="${workId}"] [data-keyword-category]`).forEach((input) => {
      const words = [...new Set(input.value.split(/[、,，;；\n]+/).map((word) => word.trim()).filter(Boolean))];
      if (input.dataset.keywordCategory === '其他关键词') extra = words;
      else categories[input.dataset.keywordCategory!] = words;
    });
    if (Object.values(categories).some((words) => words.length > 8) || extra.length > 80) {
      this.setWorkMessage(workId, '每类最多 8 个，其他关键词最多 80 个。请删减后保存。', 'is-error');
      // Keep unsaved text visible rather than rerendering the editor.
      window.alert('每类最多 8 个，其他关键词最多 80 个。请删减后保存。');
      return;
    }
    this.busyWorks.add(workId);
    try {
      const result = await instantApi.saveModelMrKeywords(workId, categories, extra, detail.work.keyword_revision || '');
      [detail.work, ...this.works, ...this.relatedWorks].filter((work) => work.id === workId).forEach((work) => {
        work.keywords = result.keywords;
        work.keyword_info = result.keyword_info;
        work.keyword_revision = result.keyword_revision;
      });
      this.editingKeywords.delete(workId);
      this.setWorkMessage(workId, '关键词已保存；未调用 AI。', 'is-done');
      this.renderWorks();
    } catch (error) {
      // Preserve the draft when the server rejects a stale revision or the network fails.
      window.alert(error instanceof Error ? error.message : '保存失败，请保留当前草稿后重试。');
    } finally { this.busyWorks.delete(workId); }
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
