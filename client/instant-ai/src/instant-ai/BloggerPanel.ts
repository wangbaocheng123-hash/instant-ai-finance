import { instantApi } from './api';
import type {
  BloggerCreator,
  BloggerLibraryStatus,
  BloggerProcessingStatus,
  BloggerTransferStatus,
  BloggerWork,
  BloggerWorkDetail,
} from './types';

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
  private requestSerial = 0;

  constructor() {
    this.element = document.createElement('article');
    this.element.className = 'finance-panel blogger-panel';
    this.element.dataset.section = 'blogger-library';
    this.element.hidden = true;
    this.element.innerHTML = `
      <header class="panel-header blogger-header">
        <div class="panel-heading"><h2>博主资料</h2><span>独立主人资料域 · 传输与处理状态</span></div>
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

    const creatorsResponse = await instantApi.bloggerCreators();
    if (requestId !== this.requestSerial) return;
    this.creators = creatorsResponse.items;
    if (this.selectedCreatorId && !this.creators.some((creator) => creator.creator_id === this.selectedCreatorId)) {
      this.selectedCreatorId = null;
      this.selectedWorkKey = null;
      this.works = [];
      this.detail = null;
    }

    if (this.selectedCreatorId) {
      const worksResponse = await instantApi.bloggerCreatorWorks(this.selectedCreatorId);
      if (requestId !== this.requestSerial) return;
      this.works = worksResponse.items;
      const creatorIndex = this.creators.findIndex((creator) => creator.creator_id === worksResponse.creator.creator_id);
      if (creatorIndex >= 0) this.creators[creatorIndex] = worksResponse.creator;
      if (this.selectedWorkKey) {
        const detail = await instantApi.bloggerWork(this.selectedWorkKey);
        if (requestId !== this.requestSerial) return;
        this.detail = detail;
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
    const target = event.target as HTMLElement;
    const action = target.closest<HTMLElement>('[data-blogger-action]');
    if (!action) return;
    const command = action.dataset.bloggerAction;
    if (command === 'open-creator' && action.dataset.creatorId) {
      await this.openCreator(action.dataset.creatorId);
    } else if (command === 'open-work' && action.dataset.workKey) {
      await this.openWork(action.dataset.workKey);
    } else if (command === 'back-creators') {
      ++this.requestSerial;
      this.selectedCreatorId = null;
      this.selectedWorkKey = null;
      this.works = [];
      this.detail = null;
      this.renderCreators();
      this.scrollPanelToTop();
    } else if (command === 'back-works') {
      ++this.requestSerial;
      this.selectedWorkKey = null;
      this.detail = null;
      this.renderWorks();
      this.scrollPanelToTop();
    }
  }

  private async openCreator(creatorId: string): Promise<void> {
    const creator = this.creators.find((item) => item.creator_id === creatorId);
    if (!creator) return;
    this.selectedCreatorId = creator.creator_id;
    this.selectedWorkKey = null;
    this.works = [];
    this.detail = null;
    const requestId = ++this.requestSerial;
    this.renderLoading(`正在读取 ${creator.display_name} 的作品…`);
    try {
      const response = await instantApi.bloggerCreatorWorks(creator.creator_id);
      if (requestId !== this.requestSerial) return;
      this.works = response.items;
      const creatorIndex = this.creators.findIndex((item) => item.creator_id === response.creator.creator_id);
      if (creatorIndex >= 0) this.creators[creatorIndex] = response.creator;
      this.renderWorks();
      this.scrollPanelToTop();
    } catch (error) {
      if (requestId !== this.requestSerial) return;
      this.renderViewError(error instanceof Error ? error.message : '博主作品读取失败。', 'back-creators');
    }
  }

  private async openWork(workKey: string): Promise<void> {
    const work = this.works.find((item) => item.work_key === workKey);
    if (!work) return;
    this.selectedWorkKey = work.work_key;
    this.detail = null;
    const requestId = ++this.requestSerial;
    this.renderLoading('正在读取作品详情…');
    try {
      const detail = await instantApi.bloggerWork(work.work_key);
      if (requestId !== this.requestSerial) return;
      this.detail = detail;
      this.renderDetail();
      this.scrollPanelToTop();
    } catch (error) {
      if (requestId !== this.requestSerial) return;
      this.renderViewError(error instanceof Error ? error.message : '作品详情读取失败。', 'back-works');
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
    const heading = this.viewHeading('全部博主', '选择博主后查看其独立作品资料。');
    const list = document.createElement('div');
    list.className = 'blogger-card-list';
    this.creators.forEach((creator) => list.append(this.renderCreatorCard(creator)));
    if (!this.creators.length) list.append(this.message('博主资料库当前没有可显示内容。'));
    root.append(heading, list);
    this.body.replaceChildren(root);
  }

  private renderLibrarySummary(status: BloggerLibraryStatus): HTMLElement {
    const summary = document.createElement('section');
    summary.className = 'blogger-library-summary';
    const definitions = [
      ['博主', status.counts.creators],
      ['作品', status.counts.works],
      ['传输中', status.counts.transferring],
      ['待批准转写', status.counts.awaiting_asr_approval],
    ] as const;
    definitions.forEach(([label, value]) => {
      const item = document.createElement('div');
      const count = document.createElement('b');
      count.textContent = String(value);
      const name = document.createElement('span');
      name.textContent = label;
      item.append(count, name);
      summary.append(item);
    });
    return summary;
  }

  private renderCreatorCard(creator: BloggerCreator): HTMLElement {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'blogger-card blogger-creator-card';
    button.dataset.bloggerAction = 'open-creator';
    button.dataset.creatorId = creator.creator_id;
    const main = document.createElement('div');
    main.className = 'blogger-card-main';
    const title = document.createElement('h3');
    title.textContent = creator.display_name || '未命名博主';
    const meta = document.createElement('p');
    meta.textContent = `${creator.platform || '平台待确认'} · ${creator.work_count} 部作品 · 最近 ${this.formatDate(creator.latest_published_at || creator.latest_captured_at)}`;
    main.append(title, meta);
    const statuses = document.createElement('div');
    statuses.className = 'blogger-card-statuses';
    if (creator.status_counts.transferring) statuses.append(this.statusPill(`传输中 ${creator.status_counts.transferring}`, 'is-active'));
    if (creator.status_counts.awaiting_asr_approval) statuses.append(this.statusPill(`待批准 ${creator.status_counts.awaiting_asr_approval}`, 'is-pending'));
    if (creator.status_counts.ready) statuses.append(this.statusPill(`已就绪 ${creator.status_counts.ready}`, 'is-ready'));
    if (creator.status_counts.failed) statuses.append(this.statusPill(`异常 ${creator.status_counts.failed}`, 'is-error'));
    if (!statuses.childElementCount) statuses.append(this.statusPill('等待资料', 'is-pending'));
    const arrow = document.createElement('span');
    arrow.className = 'blogger-card-arrow';
    arrow.textContent = '›';
    button.append(main, statuses, arrow);
    return button;
  }

  private renderWorks(): void {
    const creator = this.selectedCreator();
    if (!creator) {
      this.selectedCreatorId = null;
      this.renderCreators();
      return;
    }
    const root = document.createElement('div');
    root.className = 'blogger-view blogger-works-view';
    root.append(this.backButton('全部博主', 'back-creators'));
    root.append(this.viewHeading(creator.display_name, `${creator.platform || '平台待确认'} · ${this.works.length} 部已返回作品`));
    const list = document.createElement('div');
    list.className = 'blogger-card-list';
    this.works.forEach((work) => list.append(this.renderWorkCard(work)));
    if (!this.works.length) list.append(this.message('这位博主当前没有可显示作品。'));
    root.append(list);
    this.body.replaceChildren(root);
  }

  private renderWorkCard(work: BloggerWork): HTMLElement {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'blogger-card blogger-work-card';
    button.dataset.bloggerAction = 'open-work';
    button.dataset.workKey = work.work_key;
    const main = document.createElement('div');
    main.className = 'blogger-card-main';
    const title = document.createElement('h3');
    title.textContent = work.title || work.description || '未命名作品';
    const meta = document.createElement('p');
    meta.textContent = `${this.formatDate(work.published_at || work.captured_at)} · 修订 ${work.transfer.source_revision}`;
    main.append(title, meta);
    const statuses = document.createElement('div');
    statuses.className = 'blogger-card-statuses';
    const transfer = this.transferPresentation(work.transfer.status);
    const processing = this.processingPresentation(work.processing_status);
    statuses.append(this.statusPill(transfer.label, transfer.tone), this.statusPill(processing.label, processing.tone));
    const arrow = document.createElement('span');
    arrow.className = 'blogger-card-arrow';
    arrow.textContent = '›';
    button.append(main, statuses, arrow);
    return button;
  }

  private renderDetail(): void {
    const detail = this.detail;
    if (!detail) {
      this.renderWorks();
      return;
    }
    const root = document.createElement('div');
    root.className = 'blogger-view blogger-detail-view';
    root.append(this.backButton('返回作品', 'back-works'));
    const article = document.createElement('article');
    article.className = 'blogger-work-detail';
    const kicker = document.createElement('p');
    kicker.className = 'blogger-detail-kicker';
    kicker.textContent = `${detail.platform || '平台待确认'} · ${detail.work_type || '作品'} · 修订 ${detail.transfer.source_revision}`;
    const title = document.createElement('h3');
    title.textContent = detail.title || detail.description || '未命名作品';
    const date = document.createElement('time');
    date.textContent = this.formatDate(detail.published_at || detail.captured_at);
    article.append(kicker, title, date);
    if (detail.description && detail.description !== detail.title) {
      const description = document.createElement('p');
      description.className = 'blogger-detail-description';
      description.textContent = detail.description;
      article.append(description);
    }

    const stateGrid = document.createElement('div');
    stateGrid.className = 'blogger-state-grid';
    stateGrid.append(
      this.stateCard('资料传输', this.transferPresentation(detail.transfer.status)),
      this.stateCard('处理状态', this.processingPresentation(detail.processing_status)),
    );
    article.append(stateGrid);

    const transferFacts = document.createElement('section');
    transferFacts.className = 'blogger-detail-section blogger-transfer-facts';
    const transferTitle = document.createElement('h4');
    transferTitle.textContent = '传输摘要';
    const facts = document.createElement('div');
    const transferRows: Array<readonly [string, string]> = [
      ['媒体', this.progressText(detail.transfer.media_received, detail.transfer.media_expected)],
      ['评论', this.progressText(detail.transfer.comments_received, detail.transfer.comments_expected)],
      ['接收时间', this.formatDate(detail.transfer.received_at)],
    ];
    transferRows.forEach(([label, value]) => {
      const row = document.createElement('div');
      const name = document.createElement('span');
      name.textContent = label;
      const content = document.createElement('b');
      content.textContent = value;
      row.append(name, content);
      facts.append(row);
    });
    transferFacts.append(transferTitle, facts);
    article.append(transferFacts);

    if (detail.comment_snapshot) {
      const comments = document.createElement('section');
      comments.className = 'blogger-detail-section';
      const heading = document.createElement('h4');
      heading.textContent = '评论快照';
      const text = document.createElement('p');
      text.textContent = `已接收 ${detail.comment_snapshot.captured_count} 条，共识别 ${detail.comment_snapshot.top_level_count} 条主评论与 ${detail.comment_snapshot.reply_groups} 组回复${detail.comment_snapshot.complete ? '，快照完整。' : '，快照尚未完整。'}`;
      comments.append(heading, text);
      article.append(comments);
    }

    const safeSourceUrl = this.safeHttpsUrl(detail.source_url);
    if (safeSourceUrl) {
      const source = document.createElement('a');
      source.className = 'blogger-source-link';
      source.href = safeSourceUrl;
      source.target = '_blank';
      source.rel = 'noopener noreferrer';
      source.referrerPolicy = 'no-referrer';
      source.textContent = '核验抖音原链接';
      article.append(source);
    }
    const boundary = document.createElement('p');
    boundary.className = 'blogger-boundary-note';
    boundary.textContent = '本页只读取独立博主资料域；不读取即时财经新闻或模型先生数据。处理状态由服务端返回，客户端不会从传输状态自行推断。';
    article.append(boundary);
    root.append(article);
    this.body.replaceChildren(root);
  }

  private transferPresentation(status: BloggerTransferStatus): StatusPresentation {
    switch (status) {
      case 'transferring':
        return { label: '传输中', detail: '资料仍在安全传输，完成前不会进入后续处理。', tone: 'is-active' };
      case 'verifying':
        return { label: '校验中', detail: '正在核对传输长度与摘要。', tone: 'is-active' };
      case 'verified':
        return { label: '传输已核验', detail: '本次资料传输已经完成完整性校验。', tone: 'is-ready' };
      case 'failed':
        return { label: '传输异常', detail: '本次传输未完成，请等待采集端安全重试。', tone: 'is-error' };
      case 'manifest_received':
        return { label: '清单已接收', detail: '新加坡端已接收清单，正文资料仍按独立传输状态处理。', tone: 'is-pending' };
      default:
        return { label: '等待传输', detail: '尚未收到完整资料传输状态。', tone: 'is-pending' };
    }
  }

  private processingPresentation(status: BloggerProcessingStatus): StatusPresentation {
    switch (status) {
      case 'awaiting_asr_approval':
        return {
          label: '待批准转写',
          detail: '等待主人批准转写；系统不会自动调用付费识别，也不会自动产生费用。',
          tone: 'is-pending',
        };
      case 'transcribing':
        return { label: '转写处理中', detail: '服务端正在处理已获批准的转写任务。', tone: 'is-active' };
      case 'ready':
        return { label: '资料已就绪', detail: '作品资料已完成当前阶段处理。', tone: 'is-ready' };
      case 'failed':
        return { label: '处理异常', detail: '处理未完成，当前页面不会自动重试付费服务。', tone: 'is-error' };
      default:
        return { label: '等待资料', detail: '资料传输完成前不会进入转写审批。', tone: 'is-pending' };
    }
  }

  private stateCard(titleText: string, presentation: StatusPresentation): HTMLElement {
    const card = document.createElement('section');
    card.className = `blogger-state-card ${presentation.tone}`;
    const title = document.createElement('span');
    title.textContent = titleText;
    const label = document.createElement('b');
    label.textContent = presentation.label;
    const detail = document.createElement('p');
    detail.textContent = presentation.detail;
    card.append(title, label, detail);
    return card;
  }

  private viewHeading(titleText: string, subtitleText: string): HTMLElement {
    const heading = document.createElement('header');
    heading.className = 'blogger-view-heading';
    const title = document.createElement('h3');
    title.textContent = titleText;
    const subtitle = document.createElement('p');
    subtitle.textContent = subtitleText;
    heading.append(title, subtitle);
    return heading;
  }

  private backButton(text: string, action: 'back-creators' | 'back-works'): HTMLButtonElement {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'blogger-back-button';
    button.dataset.bloggerAction = action;
    button.textContent = `‹ ${text}`;
    return button;
  }

  private statusPill(text: string, tone: StatusPresentation['tone']): HTMLElement {
    const pill = document.createElement('span');
    pill.className = `blogger-status-pill ${tone}`;
    pill.textContent = text;
    return pill;
  }

  private renderLoading(text: string): void {
    const loading = this.message(text);
    loading.classList.add('blogger-loading');
    this.body.replaceChildren(loading);
  }

  private renderViewError(text: string, backAction: 'back-creators' | 'back-works'): void {
    const root = document.createElement('div');
    root.className = 'blogger-view blogger-view-error';
    root.append(this.backButton(backAction === 'back-creators' ? '全部博主' : '返回作品', backAction));
    const message = this.message(text);
    message.classList.add('error');
    root.append(message);
    this.body.replaceChildren(root);
  }

  private renderUnavailable(message: string): void {
    const box = document.createElement('div');
    box.className = 'blogger-unavailable';
    const mark = document.createElement('span');
    mark.textContent = '博';
    const title = document.createElement('h3');
    title.textContent = '博主资料暂未连接';
    const text = document.createElement('p');
    text.textContent = message || '请等待独立博主资料域准备完成。';
    const boundary = document.createElement('small');
    boundary.textContent = '本模块只读取主人博主资料；不会混入新闻、模型先生数据或自动付费转写。';
    box.append(mark, title, text, boundary);
    this.body.replaceChildren(box);
  }

  private selectedCreator(): BloggerCreator | null {
    return this.creators.find((creator) => creator.creator_id === this.selectedCreatorId) || null;
  }

  private progressText(received: number, expected: number): string {
    if (expected <= 0) return received > 0 ? `已接收 ${received}` : '无待传资料';
    return `已接收 ${Math.max(0, received)} / ${Math.max(0, expected)}`;
  }

  private formatDate(value: string | null): string {
    if (!value) return '时间待确认';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(date);
  }

  private safeHttpsUrl(value: string): string | null {
    try {
      const url = new URL(value);
      const hostname = url.hostname.toLowerCase();
      const douyinHost = hostname === 'douyin.com' || hostname.endsWith('.douyin.com');
      return url.protocol === 'https:' && douyinHost ? url.href : null;
    } catch {
      return null;
    }
  }

  private scrollPanelToTop(): void {
    this.body.scrollTo({ top: 0, behavior: 'auto' });
    if (window.matchMedia('(max-width: 820px)').matches) {
      this.element.scrollIntoView({ block: 'start', behavior: 'auto' });
    }
  }

  private message(text: string): HTMLElement {
    const element = document.createElement('div');
    element.className = 'panel-message';
    element.textContent = text;
    return element;
  }

  private required<T extends HTMLElement = HTMLElement>(selector: string): T {
    const element = this.element.querySelector<T>(selector);
    if (!element) throw new Error(`博主资料界面缺少元素：${selector}`);
    return element;
  }
}
