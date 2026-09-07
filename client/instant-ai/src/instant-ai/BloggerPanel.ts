import { instantApi } from './api';
import { commentThreads, isAuthorComment, rankCommentThreads, threadHasAuthorInteraction, type ModelMrCommentThread } from './ModelMrComments';
import type {
  BloggerCreator, BloggerLibraryStatus, BloggerProcessing, BloggerProcessingStatus, BloggerTransferStatus,
  BloggerWork, BloggerWorkDetail, ModelMrComment,
} from './types';

type DetailTab = 'video' | 'text' | 'comments' | 'keywords' | 'interpretation';
type CommentTab = 'author' | 'ranking' | 'stocks';
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
  private editingKeywords = false;
  private busy = false;
  private processing: BloggerProcessing | null = null;
  private processingBusy = false;
  private processingPollBusy = false;
  private processingFingerprint = '';
  private processingRefreshPending = false;
  private processingMessage = '';
  private workMessage: { text: string; tone: string } | null = null;
  private requestSerial = 0;

  constructor() {
    this.element = document.createElement('article');
    this.element.className = 'finance-panel blogger-panel';
    this.element.dataset.section = 'blogger-library';
    this.element.hidden = true;
    this.element.innerHTML = `
      <header class="panel-header blogger-header">
        <div class="panel-heading"><h2>博主资料</h2><span>视频、原文、评论、评股、关键词与解读</span></div>
        <span class="panel-count">连接中</span>
      </header>
      <div class="panel-body blogger-body"><div class="panel-message">正在读取博主资料…</div></div>`;
    this.body = this.required('.blogger-body');
    this.badge = this.required('.panel-count');
    this.element.addEventListener('click', (event) => void this.handleClick(event));
    window.setInterval(() => void this.pollProcessing(), 4_000);
  }

  async refresh(): Promise<void> {
    const requestId = ++this.requestSerial;
    const [status, processing] = await Promise.all([
      instantApi.bloggerLibraryStatus(),
      instantApi.bloggerProcessing(),
    ]);
    if (requestId !== this.requestSerial) return;
    this.status = status;
    this.processing = processing;
    this.processingFingerprint = this.processingSignature(processing);
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
    if (['processing', 'toggle-processing', 'resume-processing', 'retry-processing'].includes(command || '')) {
      await this.updateProcessing(command || '', Number(action.dataset.jobId || 0));
    }
    else if (command === 'open-creator' && action.dataset.creatorId) await this.openCreator(action.dataset.creatorId);
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
    else if (command === 'edit-keywords') { this.editingKeywords = true; this.renderDetail(); }
    else if (command === 'cancel-keywords') { this.editingKeywords = false; this.renderDetail(); }
    else if (command === 'save-keywords') await this.saveKeywords();
    else if (command === 'extract-keywords') await this.extractKeywords();
    else if (command === 'repair-pipeline') await this.repairPipeline();
    else if (command === 'save-interpretation') await this.saveInterpretation();
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
    this.editingKeywords = false;
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
    root.append(
      this.renderCreatorSwitch(),
      this.viewHeading(creator.display_name, `${this.works.length} 部作品 · 视频、原文、评论、关键词与解读`),
      this.renderProcessing(),
    );
    const list = document.createElement('div'); list.className = 'blogger-card-list';
    this.works.forEach((work) => list.append(this.renderWorkCard(work)));
    if (!this.works.length) list.append(this.message('这位博主当前没有已推送作品。'));
    root.append(list); this.body.replaceChildren(root);
  }

  private renderProcessing(compact = false): HTMLElement {
    const section = document.createElement('section'); section.className = `model-processing blogger-processing${compact ? ' is-compact' : ''}`;
    const heading = document.createElement('header'); heading.className = 'blogger-processing-heading';
    const title = document.createElement('h3'); title.textContent = '豆包自动识别与 AI 关键词';
    const button = this.actionButton(this.processingBusy ? '读取中…' : '立即刷新', 'processing');
    button.disabled = this.processingBusy; heading.append(title, button); section.append(heading);
    if (this.processingMessage) section.append(this.message(this.processingMessage));
    const status = this.processing;
    if (!status) return section;
    const switchRow = document.createElement('div'); switchRow.className = 'blogger-processing-switch';
    const switchState = document.createElement('div');
    const switchTitle = document.createElement('b');
    switchTitle.textContent = status.enabled && status.failures < 3 ? '自动处理已开启' : status.failures >= 3 ? '连续异常，自动处理已暂停' : '自动处理已关闭';
    const switchNote = document.createElement('span');
    switchNote.textContent = status.enabled && status.failures < 3
      ? '新视频传输完成后，系统会自动识别原文并提炼关键词。'
      : '关闭期间到达的视频不会产生自动模型费用。';
    switchState.append(switchTitle, switchNote);
    const toggle = this.actionButton(
      status.enabled ? '手动关闭' : '手动开启',
      'toggle-processing',
    );
    toggle.disabled = this.processingBusy; switchRow.append(switchState, toggle); section.append(switchRow);
    if (status.enabled && status.failures >= 3) {
      const resume = this.actionButton('核对异常后恢复自动处理', 'resume-processing');
      resume.disabled = this.processingBusy; section.append(resume);
    }

    if (!compact) {
      const summary = document.createElement('div'); summary.className = 'blogger-processing-summary';
      const waiting = status.summary.queued + status.summary.quota;
      ([['等待', waiting], ['处理中', status.summary.running], ['已完成', status.summary.done], ['需处理', status.summary.configuration + status.summary.review + status.summary.conflict]] as const)
        .forEach(([label, count]) => {
          const item = document.createElement('div'); const value = document.createElement('b'); value.textContent = String(count);
          const name = document.createElement('span'); name.textContent = label; item.append(value, name); summary.append(item);
        });
      section.append(summary);
    }
    const monitor = document.createElement('p'); monitor.className = 'blogger-processing-monitor';
    monitor.textContent = `${status.worker_running ? '后台执行器运行中' : '后台执行器未运行'} · 语音识别${status.speech_configured ? '已配置' : '未配置'} · 关键词模型${status.keywords_configured ? '已配置' : '未配置'}${status.last_reconciled ? ` · 最近核对 ${this.formatEpoch(status.last_reconciled)}` : ''}`;
    section.append(monitor);
    if (!compact) {
      section.append(this.message(`只自动处理本次开启时刻之后完成传输的视频；每 4 秒显示进度，并持久补偿漏掉的到达通知。每日最多 ${status.daily_call_limit} 次模型调用，每条最多 ${status.max_video_minutes} 分钟，已有原文或关键词不会覆盖。开启前的个别漏项，请打开作品后点“一键补做”。`));
      section.append(this.message('自动原文会标记为“尚未人工核对”。手动关闭会暂停尚未发起的自动任务；已经提交给模型的单次调用不会强行中断。'));
    }
    const visibleItems = compact && this.selectedWorkKey
      ? status.items.filter((item) => item.work_key === this.selectedWorkKey).slice(0, 2)
      : status.items.slice(0, 20);
    const listTitle = document.createElement('h4'); listTitle.className = 'blogger-processing-list-title'; listTitle.textContent = compact ? '本作品处理进度' : '最近处理明细'; section.append(listTitle);
    visibleItems.forEach((item) => {
      const row = document.createElement('div'); row.className = 'model-processing-job';
      const rowHeading = document.createElement('div'); rowHeading.className = 'blogger-processing-job-heading';
      const identity = document.createElement('div'); const jobTitle = document.createElement('b'); jobTitle.textContent = item.title;
      const meta = document.createElement('span'); meta.textContent = `${item.creator_name} · ${item.mode} · ${this.formatEpoch(item.updated)}`;
      identity.append(jobTitle, meta); const overall = document.createElement('strong'); overall.className = `processing-state state-${item.state}`; overall.textContent = item.message;
      rowHeading.append(identity, overall);
      const steps = document.createElement('div'); steps.className = 'blogger-processing-steps';
      steps.append(
        this.processingStep('视频原文', item.steps.asr.state, item.steps.asr.message),
        this.processingStep('AI关键词', item.steps.keywords.state, item.steps.keywords.message),
      );
      row.append(rowHeading, steps);
      if (['review', 'configuration'].includes(item.state)) {
        const retry = this.actionButton('核对后重试', 'retry-processing');
        retry.dataset.jobId = String(item.id); retry.disabled = this.processingBusy; row.append(retry);
      }
      section.append(row);
    });
    if (!visibleItems.length) section.append(this.message(compact ? '本作品还没有自动或手动补做任务。' : status.enabled ? '正在监控新视频，目前没有排队任务。' : '当前没有处理记录。'));
    return section;
  }

  private async updateProcessing(action: string, jobId = 0): Promise<void> {
    if (this.processingBusy) return;
    const enable = action === 'resume-processing' || !this.processing?.enabled;
    if ((action === 'resume-processing' || (action === 'toggle-processing' && enable)) && !window.confirm('开启后，新送达的普通博主视频将自动调用豆包识别并提炼关键词，可能产生费用。不处理开启前的历史作品。确认开启？')) return;
    if (action === 'retry-processing' && !window.confirm('请先核对豆包调用记录；上次中断可能已计费。确认重试所选任务？')) return;
    this.processingBusy = true;
    try {
      if (action === 'toggle-processing' || action === 'resume-processing') await instantApi.setBloggerProcessing(enable);
      if (action === 'retry-processing') await instantApi.retryBloggerProcessing(jobId);
      this.processing = await instantApi.bloggerProcessing();
      this.processingFingerprint = this.processingSignature(this.processing);
      this.processingMessage = action === 'toggle-processing' || action === 'resume-processing'
        ? (this.processing.enabled ? '自动处理已开启，新视频到达后会自动排队。' : '自动处理已关闭。')
        : '后台串行处理，页面会自动刷新进度。';
    } catch (error) {
      this.processingMessage = this.errorText(error);
    } finally {
      this.processingBusy = false;
      this.renderCurrentView();
    }
  }

  private processingStep(labelText: string, state: string, messageText: string): HTMLElement {
    const step = document.createElement('div'); step.className = `blogger-processing-step step-${state}`;
    const label = document.createElement('b'); label.textContent = labelText;
    const message = document.createElement('span'); message.textContent = messageText;
    step.append(label, message); return step;
  }

  private processingSignature(status: BloggerProcessing): string {
    return JSON.stringify({
      enabled: status.enabled,
      failures: status.failures,
      worker: status.worker_running,
      speech: status.speech_configured,
      keywords: status.keywords_configured,
      items: status.items.map((item) => [item.id, item.state, item.phase, item.updated]),
    });
  }

  private async pollProcessing(): Promise<void> {
    if (this.element.hidden || !this.selectedCreatorId || this.processingBusy || this.processingPollBusy) return;
    this.processingPollBusy = true;
    try {
      const previous = this.processing;
      const next = await instantApi.bloggerProcessing();
      const signature = this.processingSignature(next);
      const completed = next.items.some((item) => item.state === 'done'
        && !previous?.items.some((old) => old.id === item.id && old.state === 'done'));
      this.processing = next;
      if (signature !== this.processingFingerprint) {
        this.processingFingerprint = signature;
        this.processingRefreshPending ||= completed;
      }
      if (this.processingRefreshPending && !this.hasFocusedEditor()) {
        await this.refreshSelectedContent();
        this.processingRefreshPending = false;
      } else {
        const processing = this.element.querySelector('.blogger-processing');
        processing?.replaceWith(this.renderProcessing(processing.classList.contains('is-compact')));
      }
    } catch {
      // The normal 60-second application refresh reports persistent connection errors.
    } finally {
      this.processingPollBusy = false;
    }
  }

  private async refreshSelectedContent(): Promise<void> {
    const creatorId = this.selectedCreatorId;
    const workKey = this.selectedWorkKey;
    const requestId = this.requestSerial;
    if (!creatorId) return;
    const works = await instantApi.bloggerCreatorWorks(creatorId);
    if (requestId !== this.requestSerial || creatorId !== this.selectedCreatorId) return;
    this.works = works.items; this.replaceCreator(works.creator);
    if (workKey) {
      const detail = await instantApi.bloggerWork(workKey);
      if (requestId !== this.requestSerial || workKey !== this.selectedWorkKey) return;
      this.detail = detail;
    }
    this.renderCurrentView();
  }

  private hasFocusedEditor(): boolean {
    const active = document.activeElement as HTMLElement | null;
    return Boolean(active && this.element.contains(active)
      && (active.matches('input, textarea') || active.isContentEditable));
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
    const keywordMeta = document.createElement('div'); keywordMeta.className = 'model-work-meta';
    if (work.has_video_text) keywordMeta.append(this.pill('有视频原文'));
    if (work.has_interpretation) keywordMeta.append(this.pill('有解读'));
    work.keywords.slice(0, 6).forEach((keyword) => keywordMeta.append(this.pill(keyword)));
    if (keywordMeta.childElementCount) main.append(keywordMeta);
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
    root.append(this.renderCreatorSwitch(), this.backButton('返回作品', 'back-works'), this.renderProcessing(true));
    const article = document.createElement('article'); article.className = 'blogger-work-detail';
    const header = document.createElement('header'); header.className = 'blogger-workspace-header';
    const title = document.createElement('h3'); title.textContent = detail.title || detail.description || '未命名作品';
    const headerActions = document.createElement('div'); headerActions.className = 'blogger-workspace-actions';
    headerActions.append(this.actionButton('改标题', 'edit-title'));
    if (detail.media_available && this.needsPipeline(detail)) {
      const repair = this.actionButton('一键补做原文 + AI关键词', 'repair-pipeline', true);
      repair.disabled = this.busy; headerActions.append(repair);
    }
    header.append(title, headerActions);
    article.append(header);
    if (this.editingTitle) article.append(this.renderTitleEditor(detail));
    const meta = document.createElement('p'); meta.className = 'blogger-detail-kicker';
    meta.textContent = `${this.formatDate(detail.published_at || detail.captured_at)} · ${detail.media_available ? '本地视频' : '视频待传'} · ${detail.comment_total} 条评论`;
    article.append(meta);
    const tabs = document.createElement('nav'); tabs.className = 'model-detail-tabs';
    ([
      ['video', '本地视频'],
      ['text', '视频原文'],
      ['comments', `评论 ${detail.comment_total}`],
      ['keywords', 'AI关键词'],
      ['interpretation', '解读感悟'],
    ] as const).forEach(([key, label]) => {
      const button = this.actionButton(label, 'detail-tab');
      button.dataset.detailTab = key; button.classList.toggle('is-active', this.detailTab === key); tabs.append(button);
    });
    const content = document.createElement('div'); content.className = 'model-detail-content';
    if (this.detailTab === 'video') content.append(this.renderVideo(detail));
    else if (this.detailTab === 'text') content.append(this.renderVideoText(detail));
    else if (this.detailTab === 'comments') content.append(this.renderComments(detail));
    else if (this.detailTab === 'keywords') content.append(this.renderKeywords(detail));
    else content.append(this.renderInterpretation(detail));
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
    if (detail.video_text.source === 'doubao-auto-unreviewed') source.textContent = '豆包已自动识别并保存，尚未人工核对；您可修改后保存确认。';
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
    const threads = commentThreads(detail.comments);
    const author = threads.filter(threadHasAuthorInteraction);
    const ranking = rankCommentThreads(threads);
    const tabs = document.createElement('nav'); tabs.className = 'model-comment-tabs';
    ([['author', '作者互动', author.length], ['ranking', '粉丝评论', ranking.topLiked.length + ranking.remaining.length], ['stocks', '评股', detail.stock_mentions?.items?.length || 0]] as const)
      .forEach(([key, label, count]) => {
        const button = this.actionButton(`${label} ${count}`, 'comment-tab'); button.dataset.commentTab = key;
        button.classList.toggle('is-active', this.commentTab === key); tabs.append(button);
      });
    panel.append(tabs);
    let remaining = 0;
    if (this.commentTab === 'author') {
      const note = document.createElement('p'); note.className = 'model-comment-sort-note';
      note.textContent = '红色“作者”标识表示博主本人发言；“作者赞过”表示本人点赞。保留原提问和同楼上下文，身份只信任采集端的明确标记。';
      panel.append(note);
      author.slice(0, this.commentLimit).forEach((thread) => panel.append(this.renderCommentThread(thread, true)));
      if (!author.length) panel.append(this.message('这条作品暂未识别到博主本人回复或点赞。'));
      remaining = Math.max(0, author.length - this.commentLimit);
    } else if (this.commentTab === 'ranking') {
      const note = document.createElement('p'); note.className = 'model-comment-sort-note';
      note.textContent = '高赞前十按点赞数、回复数排序；其余评论以20字以上有效文字优先。纯表情和灌水不参与；排名不代表观点正确。'; panel.append(note);
      const high = document.createElement('section'); high.className = 'model-comment-group model-high-liked';
      const highHeading = document.createElement('h4'); highHeading.textContent = `高赞前十 · ${ranking.topLiked.length} 组`; high.append(highHeading);
      ranking.topLiked.forEach((thread, index) => high.append(this.renderCommentThread(thread, false, index + 1)));
      if (!ranking.topLiked.length) high.append(this.message('暂无有点赞的有效评论。'));
      const rest = document.createElement('section'); rest.className = 'model-comment-group model-quality-comments';
      const restHeading = document.createElement('h4'); restHeading.textContent = `其余评论 · 有效长回复优先（${ranking.remaining.length} 组）`; rest.append(restHeading);
      ranking.remaining.slice(0, this.commentLimit).forEach((thread) => rest.append(this.renderCommentThread(thread, false)));
      panel.append(high, rest); remaining = Math.max(0, ranking.remaining.length - this.commentLimit);
    } else panel.append(this.renderStockMentions(detail));
    if (remaining) panel.append(this.actionButton(`继续显示（还有 ${remaining} 组）`, 'more-comments'));
    const boundary = document.createElement('p'); boundary.className = 'model-comments-note';
    boundary.textContent = `已安全读取 ${detail.comments.length} 条评论；账号编号、主页与原始采集数据不会显示。`;
    panel.append(boundary); return panel;
  }

  private renderCommentThread(thread: ModelMrCommentThread, authorMode: boolean, rank = 0): HTMLElement {
    const section = document.createElement('section');
    section.className = `model-comment-thread${authorMode ? ' is-author-thread' : ''}`;
    section.dataset.threadKey = thread.key;
    if (rank) { const badge = document.createElement('span'); badge.className = 'model-comment-rank'; badge.textContent = String(rank); section.append(badge); }
    if (thread.root) section.append(this.renderComment(thread.root));
    const replies = [...thread.replies].sort((a, b) => Number(isAuthorComment(b)) - Number(isAuthorComment(a)) || Number(b.author_liked) - Number(a.author_liked) || b.like_count - a.like_count);
    replies.slice(0, 6).forEach((comment) => section.append(this.renderComment(comment)));
    if (replies.length > 6) {
      const more = document.createElement('details'); more.className = 'model-thread-more';
      const summary = document.createElement('summary'); summary.textContent = `展开同楼其余回复（${replies.length - 6} 条）`; more.append(summary);
      let loaded = 6;
      const load = document.createElement('button'); load.type = 'button'; load.textContent = '继续显示同楼回复';
      const appendPage = () => {
        replies.slice(loaded, loaded + 30).forEach((comment) => more.insertBefore(this.renderComment(comment), load));
        loaded += 30; load.hidden = loaded >= replies.length;
      };
      load.addEventListener('click', appendPage); more.append(load);
      more.addEventListener('toggle', () => { if (more.open && loaded === 6) appendPage(); }); section.append(more);
    }
    return section;
  }

  private renderComment(comment: ModelMrComment): HTMLElement {
    const item = document.createElement('article'); const authorComment = isAuthorComment(comment);
    item.className = `model-comment${comment.reply_depth ? ' is-reply' : ''}${authorComment ? ' is-author' : ''}${comment.author_liked ? ' is-author-liked' : ''}`;
    item.dataset.commentId = String(comment.id);
    const header = document.createElement('header'); const identity = document.createElement('div'); identity.className = 'model-comment-identity';
    const author = document.createElement('b'); author.textContent = comment.author; identity.append(author);
    if (authorComment) { const badge = document.createElement('span'); badge.className = 'model-author-badge'; badge.textContent = '作者'; identity.append(badge); }
    const time = document.createElement('time'); time.textContent = this.formatDate(comment.published_at); header.append(identity, time);
    const text = document.createElement('p'); text.textContent = comment.text;
    const metrics = document.createElement('small');
    metrics.textContent = `赞 ${comment.like_count}${comment.reply_count ? ` · 回复 ${comment.reply_count}` : ''}`;
    item.append(header, text, metrics);
    if (comment.author_liked) { const liked = document.createElement('span'); liked.className = 'model-author-liked-badge'; liked.textContent = '♥ 作者赞过'; item.append(liked); }
    return item;
  }

  private renderStockMentions(detail: BloggerWorkDetail): HTMLElement {
    const report = detail.stock_mentions; const root = document.createElement('section'); root.className = 'model-stock-report';
    if (!report || (!report.method && !report.items.length)) {
      root.append(this.message('此作品尚无已同步的评股报告，不能据此判断评论中没有股票。这里不会自动猜测股票简称。'));
      return root;
    }
    const items = [...report.items].sort((a, b) => b.comment_count - a.comment_count || b.mention_count - a.mention_count || a.code.localeCompare(b.code)).slice(0, 20);
    const heading = document.createElement('header'); const title = document.createElement('b'); title.textContent = '评论区股票热度';
    const summary = document.createElement('span'); summary.textContent = `报告已检查 ${report.total_comments} 条评论 · 展示 ${items.length} / ${report.stock_count} 只股票`; heading.append(title, summary); root.append(heading);
    const explanation = document.createElement('p'); explanation.className = 'model-comment-sort-note'; explanation.textContent = '按提及股票的评论条数排序，最多展示前 20 只；热度不代表博主推荐、持仓或投资建议。'; root.append(explanation);
    const threads = commentThreads(detail.comments);
    if (!items.length) root.append(this.message('已有报告中没有可唯一识别的股票提及。'));
    items.forEach((stock, index) => {
      const row = document.createElement('details'); row.className = `model-stock-row${index < 3 ? ' is-top-stock' : ''}`;
      const rowSummary = document.createElement('summary'); const rank = document.createElement('span'); rank.className = 'model-stock-rank'; rank.textContent = String(index + 1);
      const identity = document.createElement('div'); identity.className = 'model-stock-identity'; const name = document.createElement('b'); name.textContent = stock.name;
      const code = document.createElement('small'); code.textContent = stock.code; const breakdown = document.createElement('small'); breakdown.className = 'model-stock-breakdown'; breakdown.textContent = `粉丝 ${stock.fan_comment_count} · 作者 ${stock.author_comment_count}`; identity.append(name, code, breakdown);
      const count = document.createElement('span'); count.className = 'model-stock-count'; count.textContent = `${stock.comment_count} 条评论`; rowSummary.append(rank, identity, count); row.append(rowSummary);
      let expanded = false;
      row.addEventListener('toggle', () => {
        if (!row.open || expanded) return; expanded = true; const ids = new Set(stock.comment_ids);
        const matched = threads.filter((thread) => [thread.root, ...thread.replies].some((comment) => comment && ids.has(comment.id)));
        if (matched.length) matched.slice(0, 20).forEach((thread) => row.append(this.renderCommentThread(thread, false)));
        else stock.examples.forEach((example) => { const text = document.createElement('p'); text.className = 'model-stock-example'; text.textContent = `已保存的报告摘录：${example}`; row.append(text); });
      }); root.append(row);
    });
    if (report.uncertain.length) {
      const uncertain = document.createElement('details'); uncertain.className = 'model-stock-uncertain'; const title = document.createElement('summary'); title.textContent = `待核对简称 ${report.uncertain.length} 项（不计入排名）`; uncertain.append(title);
      report.uncertain.forEach((item) => { const line = document.createElement('p'); line.textContent = `${item.text} · ${item.comment_count} 条${item.candidates.length ? ` · 候选：${item.candidates.join('、')}` : ''}`; uncertain.append(line); }); root.append(uncertain);
    }
    const footer = document.createElement('p'); footer.className = 'model-comments-note'; footer.textContent = report.message || '使用采集端已核验的本地证券名称表生成，不用 AI 猜测。'; root.append(footer); return root;
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

  private async repairPipeline(): Promise<void> {
    if (!this.detail || this.busy || !this.needsPipeline(this.detail)) return;
    if (!window.confirm('只补做这条作品缺少的步骤：豆包识别视频原文、再提炼 AI 关键词，可能产生费用；已有内容不会覆盖。确认排队？')) return;
    this.busy = true; this.setWorkMessage('正在把这条作品加入补做队列…', '');
    try {
      const result = await instantApi.processBloggerWork(this.detail.work_key);
      this.processing = await instantApi.bloggerProcessing();
      this.processingFingerprint = this.processingSignature(this.processing);
      this.processingMessage = '单条补做已加入后台队列，页面每 4 秒自动显示进度。';
      this.workMessage = { text: result.message || '已加入补做队列。', tone: result.state === 'done' ? 'is-done' : '' };
    } catch (error) { this.workMessage = { text: this.errorText(error), tone: 'is-error' }; }
    finally { this.busy = false; this.renderDetail(); }
  }

  private needsPipeline(detail: BloggerWorkDetail): boolean {
    const keywordInfo = detail.keyword_info;
    const hasKeywords = Boolean(detail.keywords.length || keywordInfo?.confirmed_at || keywordInfo?.schema_version);
    return !detail.video_text.text.trim() || !hasKeywords;
  }

  private renderKeywords(detail: BloggerWorkDetail): HTMLElement {
    const panel = document.createElement('div'); panel.className = 'model-keyword-panel';
    const info = detail.keyword_info;
    const note = document.createElement('p');
    note.textContent = info?.edited_by_owner ? '已保存的主人整理结果；不会自动重提炼。' : '显示已保存的 AI 提炼结果；查看和手动整理不调用 AI。';
    panel.append(note);
    const extract = this.actionButton('豆包 AI 提炼关键词', 'extract-keywords');
    extract.disabled = !detail.video_text.text.trim() || this.busy || this.editingKeywords; panel.append(extract);
    if (info?.stale) panel.append(this.message('原文可能已变化，请核对已有关键词；本页不会自动产生新结果。'));
    const groups = Object.entries(info?.categories || {});
    const categorized = new Set(groups.flatMap(([, words]) => words));
    const extra = detail.keywords.filter((word) => !categorized.has(word));
    [...groups, ['其他关键词', extra] as [string, string[]]].forEach(([name, words]) => {
      if (!words.length && !this.editingKeywords) return;
      const group = document.createElement('section'); const title = document.createElement('h4'); title.textContent = name; group.append(title);
      if (this.editingKeywords) {
        const input = document.createElement('textarea'); input.dataset.keywordCategory = name; input.setAttribute('aria-label', name);
        input.value = words.join('、'); input.maxLength = name === '其他关键词' ? 5000 : 600; group.append(input);
      } else words.forEach((word) => group.append(this.pill(word)));
      panel.append(group);
    });
    if (!detail.keywords.length && !this.editingKeywords) panel.append(this.message('尚无已保存关键词。可基于已保存的视频原文点击豆包提炼。'));
    if (this.editingKeywords) {
      panel.append(this.message('用顿号、逗号或换行分隔；每类最多 8 个关键词。'));
      const save = this.actionButton('保存关键词', 'save-keywords', true); save.disabled = this.busy;
      panel.append(save, this.actionButton('取消整理', 'cancel-keywords'));
    } else if (detail.keyword_revision) panel.append(this.actionButton('手动整理关键词', 'edit-keywords'));
    return panel;
  }

  private async extractKeywords(): Promise<void> {
    if (!this.detail || this.busy) return;
    if (!window.confirm('只根据已保存的视频原文调用豆包提炼十类关键词，可能产生模型费用。确认继续？')) return;
    this.busy = true; this.setWorkMessage('正在排队提炼关键词…', '');
    try {
      const result = await instantApi.extractBloggerKeywords(this.detail.work_key, this.detail.keyword_revision || '');
      this.workMessage = { text: `${result.message}。可在作品页“豆包自动处理”查看状态。`, tone: 'is-done' };
    } catch (error) { this.workMessage = { text: this.errorText(error), tone: 'is-error' }; }
    finally { this.busy = false; this.renderDetail(); }
  }

  private async saveKeywords(): Promise<void> {
    if (!this.detail || this.busy) return;
    const categories: Record<string, string[]> = {}; let extra: string[] = [];
    this.element.querySelectorAll<HTMLTextAreaElement>('[data-keyword-category]').forEach((input) => {
      const words = [...new Set(input.value.split(/[、,，;；\n]+/).map((word) => word.trim()).filter(Boolean))];
      if (input.dataset.keywordCategory === '其他关键词') extra = words;
      else categories[input.dataset.keywordCategory!] = words;
    });
    if (Object.values(categories).some((words) => words.length > 8) || extra.length > 80) {
      window.alert('每类最多 8 个，其他关键词最多 80 个。请删减后保存。'); return;
    }
    this.busy = true;
    try {
      const result = await instantApi.saveBloggerKeywords(this.detail.work_key, categories, extra, this.detail.keyword_revision || '');
      this.detail.keywords = result.keywords; this.detail.keyword_info = result.keyword_info; this.detail.keyword_revision = result.keyword_revision;
      const work = this.works.find((item) => item.work_key === this.detail?.work_key);
      if (work) { work.keywords = result.keywords; work.keyword_info = result.keyword_info; work.keyword_revision = result.keyword_revision; }
      this.editingKeywords = false; this.workMessage = { text: '关键词已保存；未调用 AI。', tone: 'is-done' };
    } catch (error) { window.alert(this.errorText(error)); }
    finally { this.busy = false; this.renderDetail(); }
  }

  private renderInterpretation(detail: BloggerWorkDetail): HTMLElement {
    const panel = document.createElement('div'); panel.className = 'model-video-text-panel';
    const text = document.createElement('textarea'); text.id = 'blogger-interpretation'; text.maxLength = 200000;
    text.value = detail.interpretation.text || ''; text.placeholder = '尚未保存解读感悟。这里不会自动调用 AI。';
    const actions = document.createElement('div'); actions.className = 'model-text-actions';
    const save = this.actionButton('保存解读感悟', 'save-interpretation', true); save.disabled = this.busy;
    actions.append(save); panel.append(text, actions); return panel;
  }

  private async saveInterpretation(): Promise<void> {
    if (!this.detail || this.busy) return;
    const value = this.element.querySelector<HTMLTextAreaElement>('#blogger-interpretation')?.value || '';
    this.busy = true; this.setWorkMessage('正在保存解读感悟…', '');
    try {
      const result = await instantApi.saveBloggerInterpretation(this.detail.work_key, value);
      this.detail.interpretation = { text: result.text, updated_at: new Date().toISOString() };
      this.detail.has_interpretation = Boolean(result.text);
      const work = this.works.find((item) => item.work_key === this.detail?.work_key); if (work) work.has_interpretation = Boolean(result.text);
      this.workMessage = { text: '解读感悟已保存；未调用 AI。', tone: 'is-done' };
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
  private pill(text: string): HTMLElement {
    const pill = document.createElement('span'); pill.className = 'model-pill'; pill.textContent = text; return pill;
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
  private resetSelection(): void { this.selectedCreatorId = null; this.selectedWorkKey = null; this.works = []; this.detail = null; this.workMessage = null; this.editingKeywords = false; }
  private selectedCreator(): BloggerCreator | null { return this.creators.find((creator) => creator.creator_id === this.selectedCreatorId) || null; }
  private formatDate(value: string | null): string {
    if (!value) return '时间待确认'; const date = new Date(value); if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
  }
  private formatEpoch(value: number): string {
    if (!value) return '尚未运行';
    return this.formatDate(new Date(value * 1_000).toISOString());
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
