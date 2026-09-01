import { instantApi } from './api';
import type {
  BloggerCreator, BloggerLibraryStatus, BloggerProcessingStatus, BloggerTransferStatus,
  BloggerWork, BloggerWorkDetail, ModelMrComment,
} from './types';

type DetailTab = 'video' | 'text' | 'comments';
type CommentTab = 'author' | 'ranking' | 'all';
interface CommentThread { key: string; root: ModelMrComment | null; replies: ModelMrComment[] }
interface StatusPresentation {
  label: string;
  detail: string;
  tone: 'is-pending' | 'is-active' | 'is-ready' | 'is-error';
}

export class BloggerPanel {
  public readonly element: HTMLElement;
  private readonly body: HTMLElement;
  private readonly badge: HTMLElement;
  private status: BloggerLibraryStatus | null = null;
  private creators: BloggerCreator[] = [];
  private works: BloggerWork[] = [];
  private detail: BloggerWorkDetail | null = null;
  private selectedCreatorId: string | null = null;
  private selectedWorkKey: string | null = null;
  private detailTab: DetailTab = 'video';
  private commentTab: CommentTab = 'author';
  private commentLimit = 60;
  private editingTitle = false;
  private busy = false;
  private workMessage: { text: string; tone: string } | null = null;
  private requestSerial = 0;

  constructor() {
    this.element = document.createElement('article');
    this.element.className = 'finance-panel blogger-panel';
    this.element.dataset.section = 'blogger-library';
    this.element.hidden = true;
    this.element.innerHTML = `
      <header class="panel-header blogger-header">
        <div class="panel-heading"><h2>博主资料</h2><span>视频、原文、豆包识别与评论</span></div>
        <span class="panel-count">连接中</span>
      </header>
      <div class="panel-body blogger-body"><div class="panel-message">正在读取博主资料…</div></div>`;
    this.body = this.required('.blogger-body');
    this.badge = this.required('.panel-count');
    this.element.addEventListener('click', (event) => void this.handleClick(event));
  }

  async refresh(): Promise<void> {
    const requestId = ++this.requestSerial;
    const status = await instantApi.bloggerLibraryStatus();
    if (requestId !== this.requestSerial) return;
    this.status = status;
    if (!status.available) {
      this.badge.textContent = '未连接';
      this.renderUnavailable(status.message);
      return;
    }
    const response = await instantApi.bloggerCreators();
    if (requestId !== this.requestSerial) return;
    this.creators = response.items;
    if (this.selectedCreatorId && !this.creators.some((item) => item.creator_id === this.selectedCreatorId)) this.resetSelection();
    if (this.selectedCreatorId) {
      const works = await instantApi.bloggerCreatorWorks(this.selectedCreatorId);
      if (requestId !== this.requestSerial) return;
      this.works = works.items;
      this.replaceCreator(works.creator);
      if (this.selectedWorkKey) {
        this.detail = await instantApi.bloggerWork(this.selectedWorkKey);
        if (requestId !== this.requestSerial) return;
      }
    }
    this.badge.textContent = `${status.counts.creators} 位 · ${status.counts.works} 部`;
    this.renderCurrentView();
  }

  setError(message: string): void {
    ++this.requestSerial;
    this.badge.textContent = '异常';
    this.renderUnavailable(message || '博主资料暂时无法读取。');
  }

  private async handleClick(event: MouseEvent): Promise<void> {
    const action = (event.target as HTMLElement).closest<HTMLElement>('[data-blogger-action]');
    if (!action) return;
    const command = action.dataset.bloggerAction;
    if (command === 'open-creator' && action.dataset.creatorId) await this.openCreator(action.dataset.creatorId);
    else if (command === 'open-work' && action.dataset.workKey) await this.openWork(action.dataset.workKey);
    else if (command === 'back-creators') { ++this.requestSerial; this.resetSelection(); this.renderCreators(); }
    else if (command === 'back-works') { ++this.requestSerial; this.selectedWorkKey = null; this.detail = null; this.renderWorks(); }
    else if (command === 'detail-tab' && action.dataset.detailTab) { this.detailTab = action.dataset.detailTab as DetailTab; this.renderDetail(); }
    else if (command === 'comment-tab' && action.dataset.commentTab) { this.commentTab = action.dataset.commentTab as CommentTab; this.commentLimit = 60; this.renderDetail(); }
    else if (command === 'more-comments') { this.commentLimit += 60; this.renderDetail(); }
    else if (command === 'edit-title') {
      this.editingTitle = true; this.renderDetail();
      requestAnimationFrame(() => this.element.querySelector<HTMLInputElement>('#blogger-title-editor')?.focus());
    }
    else if (command === 'cancel-title') { this.editingTitle = false; this.renderDetail(); }
    else if (command === 'save-title') await this.saveTitle();
    else if (command === 'save-text') await this.saveVideoText();
    else if (command === 'transcribe') await this.transcribe('video');
    else if (command === 'doubao') await this.transcribe('doubao');
  }

  private async openCreator(creatorId: string): Promise<void> {
    const creator = this.creators.find((item) => item.creator_id === creatorId);
    if (!creator) return;
    this.selectedCreatorId = creatorId;
    this.selectedWorkKey = null;
    this.detail = null;
    const requestId = ++this.requestSerial;
    this.renderLoading(`正在读取 ${creator.display_name} 的作品…`);
    try {
      const response = await instantApi.bloggerCreatorWorks(creatorId);
      if (requestId !== this.requestSerial) return;
      this.works = response.items;
      this.replaceCreator(response.creator);
      this.renderWorks();
      this.scrollPanelToTop();
    } catch (error) {
      if (requestId === this.requestSerial) this.renderViewError(this.errorText(error), 'back-creators');
    }
  }

  private async openWork(workKey: string): Promise<void> {
    if (!this.works.some((item) => item.work_key === workKey)) return;
    this.selectedWorkKey = workKey;
    this.detail = null;
    this.detailTab = 'video';
    this.commentTab = 'author';
    this.commentLimit = 60;
    this.workMessage = null;
    const requestId = ++this.requestSerial;
    this.renderLoading('正在读取作品视频与评论…');
    try {
      this.detail = await instantApi.bloggerWork(workKey);
      if (requestId !== this.requestSerial) return;
      if (!this.detail.media_available) this.detailTab = 'text';
      this.renderDetail();
      this.scrollPanelToTop();
    } catch (error) {
      if (requestId === this.requestSerial) this.renderViewError(this.errorText(error), 'back-works');
    }
  }

  private renderCurrentView(): void {
    if (this.detail && this.selectedWorkKey) this.renderDetail();
    else if (this.selectedCreatorId) this.renderWorks();
    else this.renderCreators();
  }

  private renderCreators(): void {
    const root = document.createElement('div');
    root.className = 'blogger-view blogger-creators-view';
    if (this.status) root.append(this.renderLibrarySummary(this.status));
    const heading = this.viewHeading('全部博主', '选择博主后查看与模型先生一致的作品资料。');
    heading.append(this.addCreatorLink());
    const list = document.createElement('div');
    list.className = 'blogger-card-list';
    this.creators.forEach((creator) => list.append(this.renderCreatorCard(creator)));
    if (!this.creators.length) list.append(this.message('还没有博主资料，可从北京采集端新增博主。'));
    root.append(heading, list);
    this.body.replaceChildren(root);
  }

  private renderLibrarySummary(status: BloggerLibraryStatus): HTMLElement {
    const summary = document.createElement('section');
    summary.className = 'blogger-library-summary';
    ([['博主', status.counts.creators], ['作品', status.counts.works], ['传输中', status.counts.transferring], ['待识别', status.counts.awaiting_asr_approval]] as const)
      .forEach(([label, value]) => {
        const item = document.createElement('div');
        const count = document.createElement('b'); count.textContent = String(value);
        const name = document.createElement('span'); name.textContent = label;
        item.append(count, name); summary.append(item);
      });
    return summary;
  }

  private renderCreatorSwitch(): HTMLElement {
    const nav = document.createElement('nav');
    nav.className = 'blogger-creator-switch';
    this.creators.forEach((creator) => {
      const button = this.actionButton(creator.display_name || '未命名博主', 'open-creator');
      button.dataset.creatorId = creator.creator_id;
      button.classList.toggle('is-active', creator.creator_id === this.selectedCreatorId);
      nav.append(button);
    });
    nav.append(this.addCreatorLink());
    return nav;
  }

  private addCreatorLink(): HTMLAnchorElement {
    const link = document.createElement('a');
    link.className = 'blogger-add-creator';
    link.href = 'https://collector.amuyeye.com/';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = '+ 新增博主';
    link.title = '在北京采集端新增博主，采集后自动推送到这里';
    return link;
  }

  private renderCreatorCard(creator: BloggerCreator): HTMLElement {
    const button = this.actionButton('', 'open-creator');
    button.className = 'blogger-card blogger-creator-card';
    button.dataset.creatorId = creator.creator_id;
    const main = document.createElement('div'); main.className = 'blogger-card-main';
    const title = document.createElement('h3'); title.textContent = creator.display_name || '未命名博主';
    const meta = document.createElement('p');
    meta.textContent = `${creator.platform || '平台待确认'} · ${creator.work_count} 部作品 · 最近 ${this.formatDate(creator.latest_published_at || creator.latest_captured_at)}`;
    main.append(title, meta);
    const statuses = document.createElement('div'); statuses.className = 'blogger-card-statuses';
    if (creator.status_counts.awaiting_asr_approval) statuses.append(this.statusPill(`待识别 ${creator.status_counts.awaiting_asr_approval}`, 'is-pending'));
    if (creator.status_counts.ready) statuses.append(this.statusPill(`已就绪 ${creator.status_counts.ready}`, 'is-ready'));
    const arrow = document.createElement('span'); arrow.className = 'blogger-card-arrow'; arrow.textContent = '›';
    button.append(main, statuses, arrow);
    return button;
  }

  private renderWorks(): void {
    const creator = this.selectedCreator();
    if (!creator) { this.renderCreators(); return; }
    const root = document.createElement('div'); root.className = 'blogger-view blogger-works-view';
    root.append(this.renderCreatorSwitch(), this.viewHeading(creator.display_name, `${this.works.length} 部作品 · 点击进入视频、原文与评论`));
    const list = document.createElement('div'); list.className = 'blogger-card-list';
    this.works.forEach((work) => list.append(this.renderWorkCard(work)));
    if (!this.works.length) list.append(this.message('这位博主当前没有已推送作品。'));
    root.append(list); this.body.replaceChildren(root);
  }

  private renderWorkCard(work: BloggerWork): HTMLElement {
    const button = this.actionButton('', 'open-work');
    button.className = 'blogger-card blogger-work-card';
    button.dataset.workKey = work.work_key;
    const main = document.createElement('div'); main.className = 'blogger-card-main';
    const title = document.createElement('h3'); title.textContent = work.title || work.description || '未命名作品';
    const meta = document.createElement('p');
    meta.textContent = `${this.formatDate(work.published_at || work.captured_at)} · ${work.media_available ? '本地视频' : '视频待传'} · ${work.comment_count} 条评论`;
    main.append(title, meta);
    const statuses = document.createElement('div'); statuses.className = 'blogger-card-statuses';
    const transfer = this.transferPresentation(work.transfer.status);
    const processing = this.processingPresentation(work.processing_status);
    statuses.append(this.statusPill(transfer.label, transfer.tone), this.statusPill(processing.label, processing.tone));
    const arrow = document.createElement('span'); arrow.className = 'blogger-card-arrow'; arrow.textContent = '›';
    button.append(main, statuses, arrow);
    return button;
  }

  private renderDetail(): void {
    const detail = this.detail;
    if (!detail) { this.renderWorks(); return; }
    const root = document.createElement('div'); root.className = 'blogger-view blogger-detail-view';
    root.append(this.renderCreatorSwitch(), this.backButton('返回作品', 'back-works'));
    const article = document.createElement('article'); article.className = 'blogger-work-detail';
    const header = document.createElement('header'); header.className = 'blogger-workspace-header';
    const title = document.createElement('h3'); title.textContent = detail.title || detail.description || '未命名作品';
    header.append(title, this.actionButton('改标题', 'edit-title'));
    article.append(header);
    if (this.editingTitle) article.append(this.renderTitleEditor(detail));
    const meta = document.createElement('p'); meta.className = 'blogger-detail-kicker';
    meta.textContent = `${this.formatDate(detail.published_at || detail.captured_at)} · ${detail.media_available ? '本地视频' : '视频待传'} · ${detail.comment_total} 条评论`;
    article.append(meta);
    const tabs = document.createElement('nav'); tabs.className = 'model-detail-tabs';
    ([['video', '本地视频'], ['text', '视频原文'], ['comments', `评论 ${detail.comment_total}`]] as const).forEach(([key, label]) => {
      const button = this.actionButton(label, 'detail-tab');
      button.dataset.detailTab = key; button.classList.toggle('is-active', this.detailTab === key); tabs.append(button);
    });
    const content = document.createElement('div'); content.className = 'model-detail-content';
    if (this.detailTab === 'video') content.append(this.renderVideo(detail));
    else if (this.detailTab === 'text') content.append(this.renderVideoText(detail));
    else content.append(this.renderComments(detail));
    if (this.workMessage || this.busy) {
      const message = document.createElement('p'); message.className = `model-work-status ${this.workMessage?.tone || ''}`;
      message.textContent = this.workMessage?.text || '正在处理…'; content.append(message);
    }
    article.append(tabs, content, this.renderTransferSummary(detail));
    const safeSourceUrl = this.safeHttpsUrl(detail.source_url);
    if (safeSourceUrl) {
      const source = document.createElement('a'); source.className = 'blogger-source-link';
      source.href = safeSourceUrl; source.target = '_blank'; source.rel = 'noopener noreferrer'; source.referrerPolicy = 'no-referrer';
      source.textContent = '核验抖音原链接'; article.append(source);
    }
    root.append(article); this.body.replaceChildren(root);
  }

  private renderVideo(detail: BloggerWorkDetail): HTMLElement {
    const panel = document.createElement('div'); panel.className = 'model-video-panel';
    if (detail.media_available && detail.video_url) {
      const video = document.createElement('video');
      video.controls = true; video.playsInline = true; video.preload = 'metadata'; video.src = detail.video_url;
      const note = document.createElement('p'); note.textContent = '正在读取视频信息…';
      video.addEventListener('loadedmetadata', () => {
        const seconds = Number.isFinite(video.duration) ? Math.max(1, Math.round(video.duration)) : 0;
        note.textContent = `博主本地视频已就绪${seconds ? ` · ${Math.floor(seconds / 60)}分${seconds % 60}秒` : ''}。`;
      });
      video.addEventListener('error', () => { note.textContent = '本地视频加载失败，请收起后重试。'; note.classList.add('is-error'); });
      panel.append(video, note);
    } else panel.append(this.message('这条作品的视频尚未传输完成。'));
    return panel;
  }

  private renderVideoText(detail: BloggerWorkDetail): HTMLElement {
    const panel = document.createElement('div'); panel.className = 'model-video-text-panel';
    const text = document.createElement('textarea'); text.id = 'blogger-video-text';
    text.value = detail.video_text.text || detail.transcripts[0]?.text || '';
    text.placeholder = '尚无视频原文，可读取现有识别结果或使用豆包识别。'; text.maxLength = 200000;
    const source = document.createElement('p'); source.className = 'model-text-source';
    source.textContent = detail.video_text.official ? `当前来源：${detail.video_text.source}（正式原文）` : '识别结果请核对后保存为正式原文。';
    const actions = document.createElement('div'); actions.className = 'model-text-actions';
    const cached = this.actionButton('识别视频文字', 'transcribe');
    const doubao = this.actionButton('豆包识别文字', 'doubao', true);
    const save = this.actionButton('保存正式原文', 'save-text');
    cached.disabled = this.busy || !detail.capabilities.transcribe_video;
    doubao.disabled = this.busy || !detail.capabilities.doubao_asr;
    save.disabled = this.busy || !detail.capabilities.save_video_text;
    actions.append(cached, doubao, save); panel.append(text, source, actions);
    return panel;
  }

  private renderComments(detail: BloggerWorkDetail): HTMLElement {
    const panel = document.createElement('div'); panel.className = 'model-comments-panel';
    const threads = this.commentThreads(detail.comments);
    const author = threads.filter((thread) => this.threadHasAuthorInteraction(thread));
    const ranking = threads.filter((thread) => {
      const lead = thread.root || thread.replies[0];
      return lead && !this.isAuthorComment(lead) && !this.isLowValueComment(lead.text);
    }).sort((left, right) => this.compareThreads(left, right));
    const tabs = document.createElement('nav'); tabs.className = 'model-comment-tabs';
    ([['author', '本人互动', author.length], ['ranking', '评论排行', ranking.length], ['all', '全部评论', threads.length]] as const)
      .forEach(([key, label, count]) => {
        const button = this.actionButton(`${label} ${count}`, 'comment-tab'); button.dataset.commentTab = key;
        button.classList.toggle('is-active', this.commentTab === key); tabs.append(button);
      });
    panel.append(tabs);
    const source = this.commentTab === 'author' ? author : this.commentTab === 'ranking' ? ranking : threads;
    const note = document.createElement('p'); note.className = 'model-comment-sort-note';
    note.textContent = this.commentTab === 'author' ? '显示博主本人回复或本人点赞过的评论，并保留提问上下文。'
      : this.commentTab === 'ranking' ? '按点赞、回复数和有效正文长度排序，过滤纯表情等低价值评论。' : '按北京采集端保存的评论顺序显示。';
    panel.append(note);
    source.slice(0, this.commentLimit).forEach((thread, index) => panel.append(this.renderCommentThread(thread, this.commentTab === 'author', this.commentTab === 'ranking' ? index + 1 : 0)));
    if (!source.length) panel.append(this.message('当前视图暂无评论。'));
    if (source.length > this.commentLimit) panel.append(this.actionButton(`继续显示（还有 ${source.length - this.commentLimit} 组）`, 'more-comments'));
    const boundary = document.createElement('p'); boundary.className = 'model-comments-note';
    boundary.textContent = `已安全读取 ${detail.comments.length} 条评论；账号编号、主页与原始采集数据不会显示。`;
    panel.append(boundary); return panel;
  }

  private commentThreads(comments: ModelMrComment[]): CommentThread[] {
    const map = new Map<string, CommentThread>();
    comments.forEach((comment) => {
      const key = comment.thread_key || `comment-${comment.id}`;
      const thread = map.get(key) || { key, root: null, replies: [] };
      if (!comment.reply_depth && !thread.root) thread.root = comment; else thread.replies.push(comment);
      map.set(key, thread);
    });
    return Array.from(map.values());
  }

  private renderCommentThread(thread: CommentThread, authorMode: boolean, rank = 0): HTMLElement {
    const section = document.createElement('section');
    section.className = `model-comment-thread${authorMode ? ' is-author-thread' : ''}`;
    if (rank) { const badge = document.createElement('span'); badge.className = 'model-comment-rank'; badge.textContent = String(rank); section.append(badge); }
    if (thread.root) section.append(this.renderComment(thread.root));
    [...thread.replies].sort((a, b) => Number(this.isAuthorComment(b)) - Number(this.isAuthorComment(a)) || b.like_count - a.like_count)
      .slice(0, authorMode ? 20 : 6).forEach((comment) => section.append(this.renderComment(comment)));
    return section;
  }

  private renderComment(comment: ModelMrComment): HTMLElement {
    const item = document.createElement('article'); const authorComment = this.isAuthorComment(comment);
    item.className = `model-comment${comment.reply_depth ? ' is-reply' : ''}${authorComment ? ' is-author' : ''}`;
    const header = document.createElement('header'); const author = document.createElement('b');
    author.textContent = authorComment ? `${comment.author} · 作者` : comment.author;
    const time = document.createElement('time'); time.textContent = this.formatDate(comment.published_at); header.append(author, time);
    const text = document.createElement('p'); text.textContent = comment.text;
    const metrics = document.createElement('small');
    metrics.textContent = `赞 ${comment.like_count}${comment.reply_count ? ` · 回复 ${comment.reply_count}` : ''}${comment.author_liked ? ' · 作者点赞' : ''}`;
    item.append(header, text, metrics); return item;
  }

  private threadHasAuthorInteraction(thread: CommentThread): boolean {
    return [thread.root, ...thread.replies].some((comment) => comment && (this.isAuthorComment(comment) || comment.author_liked));
  }
  private isAuthorComment(comment: ModelMrComment): boolean { return comment.kind.includes('author'); }
  private isLowValueComment(text: string): boolean {
    const compact = text.replace(/\[[^\]]{1,12}\]/g, '').replace(/@\S+/g, '').replace(/[^A-Za-z0-9\u4e00-\u9fff]+/g, '').toLowerCase();
    return !compact || /^(哈|呵|嘿|嘻){1,12}$/.test(compact) || /^6{1,12}$/.test(compact)
      || new Set(['嗯', '哦', '啊', '好', '赞', '点赞', '支持', '收到', '路过', '来了', '谢谢', '感谢', '学习了']).has(compact);
  }
  private compareThreads(left: CommentThread, right: CommentThread): number {
    const a = left.root || left.replies[0]; const b = right.root || right.replies[0];
    return (b?.like_count || 0) - (a?.like_count || 0) || (b?.reply_count || b?.text.length || 0) - (a?.reply_count || a?.text.length || 0);
  }

  private renderTitleEditor(detail: BloggerWorkDetail): HTMLElement {
    const editor = document.createElement('div'); editor.className = 'model-title-editor';
    const input = document.createElement('input'); input.id = 'blogger-title-editor'; input.value = detail.title; input.maxLength = 120;
    editor.append(input, this.actionButton('保存标题', 'save-title', true), this.actionButton('取消', 'cancel-title')); return editor;
  }

  private async saveTitle(): Promise<void> {
    if (!this.detail || this.busy) return;
    const title = this.element.querySelector<HTMLInputElement>('#blogger-title-editor')?.value.trim() || '';
    if (!title) { this.setWorkMessage('作品标题不能为空。', 'is-error'); return; }
    this.busy = true; this.setWorkMessage('正在保存标题…', '');
    try {
      const result = await instantApi.saveBloggerTitle(this.detail.work_key, title);
      this.detail.title = result.title;
      const work = this.works.find((item) => item.work_key === this.detail?.work_key); if (work) work.title = result.title;
      this.editingTitle = false; this.workMessage = { text: '标题已保存。', tone: 'is-done' };
    } catch (error) { this.workMessage = { text: this.errorText(error), tone: 'is-error' }; }
    finally { this.busy = false; this.renderDetail(); }
  }

  private async saveVideoText(): Promise<void> {
    if (!this.detail || this.busy) return;
    const text = this.element.querySelector<HTMLTextAreaElement>('#blogger-video-text')?.value || '';
    this.busy = true; this.setWorkMessage('正在保存视频原文…', '');
    try {
      const result = await instantApi.saveBloggerVideoText(this.detail.work_key, text);
      this.detail.video_text = { text: result.text, official: Boolean(result.text), source: '主人保存', updated_at: new Date().toISOString() };
      this.detail.has_video_text = Boolean(result.text); this.workMessage = { text: '视频原文已保存。', tone: 'is-done' };
    } catch (error) { this.workMessage = { text: this.errorText(error), tone: 'is-error' }; }
    finally { this.busy = false; this.renderDetail(); }
  }

  private async transcribe(engine: 'video' | 'doubao'): Promise<void> {
    if (!this.detail || this.busy) return;
    if (engine === 'doubao' && !window.confirm('豆包识别会提取本地视频音频并按音频时长调用付费接口。确认继续吗？')) return;
    this.busy = true; this.setWorkMessage(engine === 'doubao' ? '正在调用豆包识别…' : '正在读取识别文字…', '');
    try {
      const result = await instantApi.transcribeBloggerWork(this.detail.work_key, engine);
      this.detail.transcripts = [{ text: result.text, source: result.engine, language: 'zh-CN', created_at: new Date().toISOString() }];
      this.workMessage = { text: result.message || '识别完成，请核对后保存正式原文。', tone: 'is-done' };
    } catch (error) { this.workMessage = { text: this.errorText(error), tone: 'is-error' }; }
    finally { this.busy = false; this.renderDetail(); }
  }

  private renderTransferSummary(detail: BloggerWorkDetail): HTMLElement {
    const section = document.createElement('details'); section.className = 'blogger-detail-section blogger-transfer-facts';
    const summary = document.createElement('summary'); summary.textContent = '采集与传输状态';
    const states = document.createElement('div'); states.className = 'blogger-state-grid';
    states.append(this.stateCard('资料传输', this.transferPresentation(detail.transfer.status)), this.stateCard('处理状态', this.processingPresentation(detail.processing_status)));
    section.append(summary, states); return section;
  }

  private transferPresentation(status: BloggerTransferStatus): StatusPresentation {
    if (status === 'verified') return { label: '传输已核验', detail: '视频和评论已完成完整性校验。', tone: 'is-ready' };
    if (status === 'failed') return { label: '传输异常', detail: '请等待北京采集端安全重试。', tone: 'is-error' };
    if (status === 'transferring' || status === 'verifying') return { label: '传输处理中', detail: '资料正在传输或校验。', tone: 'is-active' };
    return { label: '等待传输', detail: '尚未收到完整作品资料。', tone: 'is-pending' };
  }
  private processingPresentation(status: BloggerProcessingStatus): StatusPresentation {
    if (status === 'ready') return { label: '资料已就绪', detail: '作品资料已经可用。', tone: 'is-ready' };
    if (status === 'failed') return { label: '处理异常', detail: '处理失败且不会自动重试付费服务。', tone: 'is-error' };
    if (status === 'transcribing') return { label: '识别处理中', detail: '已批准的识别正在执行。', tone: 'is-active' };
    if (status === 'awaiting_asr_approval') return { label: '待识别', detail: '视频与评论可先查看；豆包只在主人确认后调用。', tone: 'is-pending' };
    return { label: '等待资料', detail: '资料完整后才能识别。', tone: 'is-pending' };
  }
  private stateCard(titleText: string, presentation: StatusPresentation): HTMLElement {
    const card = document.createElement('section'); card.className = `blogger-state-card ${presentation.tone}`;
    const title = document.createElement('span'); title.textContent = titleText;
    const label = document.createElement('b'); label.textContent = presentation.label;
    const detail = document.createElement('p'); detail.textContent = presentation.detail;
    card.append(title, label, detail); return card;
  }

  private actionButton(text: string, action: string, primary = false): HTMLButtonElement {
    const button = document.createElement('button'); button.type = 'button'; button.dataset.bloggerAction = action; button.textContent = text;
    if (primary) button.classList.add('is-primary'); return button;
  }
  private viewHeading(titleText: string, subtitleText: string): HTMLElement {
    const heading = document.createElement('header'); heading.className = 'blogger-view-heading';
    const title = document.createElement('h3'); title.textContent = titleText;
    const subtitle = document.createElement('p'); subtitle.textContent = subtitleText;
    heading.append(title, subtitle); return heading;
  }
  private backButton(text: string, action: 'back-creators' | 'back-works'): HTMLButtonElement {
    const button = this.actionButton(`‹ ${text}`, action); button.className = 'blogger-back-button'; return button;
  }
  private statusPill(text: string, tone: StatusPresentation['tone']): HTMLElement {
    const pill = document.createElement('span'); pill.className = `blogger-status-pill ${tone}`; pill.textContent = text; return pill;
  }
  private setWorkMessage(text: string, tone: string): void { this.workMessage = { text, tone }; this.renderDetail(); }
  private renderLoading(text: string): void { const loading = this.message(text); loading.classList.add('blogger-loading'); this.body.replaceChildren(loading); }
  private renderViewError(text: string, backAction: 'back-creators' | 'back-works'): void {
    const root = document.createElement('div'); root.className = 'blogger-view blogger-view-error';
    root.append(this.backButton(backAction === 'back-creators' ? '全部博主' : '返回作品', backAction));
    const message = this.message(text); message.classList.add('error'); root.append(message); this.body.replaceChildren(root);
  }
  private renderUnavailable(message: string): void {
    const box = document.createElement('div'); box.className = 'blogger-unavailable';
    const mark = document.createElement('span'); mark.textContent = '博';
    const title = document.createElement('h3'); title.textContent = '博主资料暂未连接';
    const text = document.createElement('p'); text.textContent = message || '请等待北京采集端推送博主资料。';
    box.append(mark, title, text, this.addCreatorLink()); this.body.replaceChildren(box);
  }
  private replaceCreator(creator: BloggerCreator): void {
    const index = this.creators.findIndex((item) => item.creator_id === creator.creator_id); if (index >= 0) this.creators[index] = creator;
  }
  private resetSelection(): void { this.selectedCreatorId = null; this.selectedWorkKey = null; this.works = []; this.detail = null; this.workMessage = null; }
  private selectedCreator(): BloggerCreator | null { return this.creators.find((creator) => creator.creator_id === this.selectedCreatorId) || null; }
  private formatDate(value: string | null): string {
    if (!value) return '时间待确认'; const date = new Date(value); if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
  }
  private safeHttpsUrl(value: string): string | null {
    try { const url = new URL(value); const hostname = url.hostname.toLowerCase(); return url.protocol === 'https:' && (hostname === 'douyin.com' || hostname.endsWith('.douyin.com')) ? url.href : null; }
    catch { return null; }
  }
  private scrollPanelToTop(): void {
    this.body.scrollTo({ top: 0, behavior: 'auto' });
    if (window.matchMedia('(max-width: 820px)').matches) this.element.scrollIntoView({ block: 'start', behavior: 'auto' });
  }
  private errorText(error: unknown): string { return error instanceof Error ? error.message : '操作失败。'; }
  private message(text: string): HTMLElement { const element = document.createElement('div'); element.className = 'panel-message'; element.textContent = text; return element; }
  private required<T extends HTMLElement = HTMLElement>(selector: string): T {
    const element = this.element.querySelector<T>(selector); if (!element) throw new Error(`博主资料界面缺少元素：${selector}`); return element;
  }
}
