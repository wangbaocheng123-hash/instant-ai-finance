import type { ModelMrWork } from './types';
/*! hls.js 1.7.4 — Apache-2.0; full license and notices: /hls-js-license.txt */
type AudioWork = Pick<ModelMrWork, 'id' | 'title'> & Partial<Pick<ModelMrWork, 'published_at' | 'sequence_number'>>;
type QueueTrack = AudioWork & { start: number; end: number; intro_seconds: number; body_offset: number };
type Playback = {
  audio: HTMLAudioElement; hls: import('hls.js').default | null; token: string | null;
  work: AudioWork; track: QueueTrack | null; next: boolean; minutes: number; limit: number; offset: number;
  reading: boolean; readAt: number; disposed: boolean; heard: number; lastTime: number;
};
type Transition = { target: Playback; origin: Playback; workId: number; heard: number; attempts: number;
  playRequested: boolean; readyPlaying: boolean; wasPaused: boolean; timeout: ReturnType<typeof setTimeout> };

/** List refreshes preserve the player. Settings prepare a muted successor while
 * the audible recording continues, then align it before handing over. */
export class ModelMrAudioPlayer {
  readonly element = document.createElement('section');
  private readonly title = document.createElement('strong');
  private readonly status = document.createElement('p');
  private readonly progress = document.createElement('output');
  private readonly mode = document.createElement('select');
  private readonly next = document.createElement('input');
  private readonly slot = document.createElement('div');
  private active: Playback | null = null;
  private transition: Transition | null = null;
  private ownsSession = false;

  constructor() {
    this.element.className = 'model-audio-player';
    this.element.hidden = true;
    this.element.setAttribute('aria-label', '作品音频播放器');
    const heading = document.createElement('div');
    heading.className = 'model-audio-heading';
    const stop = document.createElement('button');
    stop.type = 'button';
    stop.textContent = '停止收听';
    stop.addEventListener('click', () => this.stop());
    heading.append(this.title, stop);
    const settings = document.createElement('label');
    settings.className = 'model-audio-settings';
    settings.append('定时停止');
    this.mode.setAttribute('aria-label', '音频定时停止');
    for (const value of [0, ...Array.from({ length: 30 }, (_, index) => index + 1), 60]) {
      const option = document.createElement('option');
      option.value = String(value);
      option.textContent = value === 0 ? '不定时 · 单曲循环' : value === 60 ? '1小时后停止' : `${value}分钟后停止`;
      this.mode.append(option);
    }
    this.mode.addEventListener('change', () => this.changeSettings(true));
    settings.append(this.mode, this.progress);
    const continuous = document.createElement('label');
    continuous.className = 'model-audio-continuous';
    this.next.type = 'checkbox';
    this.next.setAttribute('aria-label', '播完自动下一条');
    continuous.append(this.next, '播完自动下一条（按序号递增）');
    this.next.addEventListener('change', () => {
      const first = this.mode.options[0];
      if (first) first.textContent = this.next.checked ? '不定时 · 播放到最新一条' : '不定时 · 单曲循环';
      this.changeSettings(false);
    });
    this.status.className = 'model-audio-status';
    const hint = document.createElement('p');
    hint.className = 'model-audio-hint';
    hint.textContent = '点击哪条，就从哪条开头听；调整连播或定时会保持当前内容和暂停状态。下一条开始前报读日期和序号。定时按播放进度跨作品累计，暂停不计时，切换连播不重新计时。请保持联网。';
    this.element.append(heading, this.slot, settings, continuous, this.status, hint);
    document.addEventListener('play', event => {
      if (event.target instanceof HTMLVideoElement && this.ownsSession) this.pauseForVideo();
    }, true);
  }

  play(work: AudioWork): void {
    this.cancelTransition();
    if (this.active) this.dispose(this.active);
    const minutes = Number(this.mode.value);
    const context = this.create(work, this.next.checked, minutes, minutes * 60, 0);
    this.active = context;
    context.audio.controls = true;
    this.slot.replaceChildren(context.audio);
    this.element.hidden = false;
    this.renderTitle(context);
    this.showStatus('正在准备音频，首次播放请稍候…');
    this.setMediaSession();
    this.load(context);
  }

  private create(work: AudioWork, next: boolean, minutes: number, limit: number, offset: number): Playback {
    const audio = document.createElement('audio');
    audio.preload = 'auto';
    audio.setAttribute('aria-label', '作品音频');
    audio.loop = !next && minutes === 0;
    const queued = next || minutes > 0;
    const context: Playback = { audio, work: { ...work }, hls: null,
      token: queued ? Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('') : null,
      track: null, next, minutes, limit, offset: queued ? offset : 0,
      reading: false, readAt: 0, disposed: false, heard: 0, lastTime: 0 };
    audio.addEventListener('play', () => {
      if (context !== this.active || context.disposed) return;
      document.querySelectorAll('video').forEach(video => video.pause());
      this.setMediaSession();
    });
    audio.addEventListener('playing', () => {
      if (this.transition?.target === context) this.transition.readyPlaying = true;
      if (context === this.active) { this.showStatus('正在播放，可锁屏收听。'); this.syncSessionState(); }
      this.tryHandoff();
    });
    audio.addEventListener('pause', () => {
      if (context !== this.active || context.disposed) return;
      if (!audio.ended && !audio.error && !this.status.classList.contains('is-error')) this.showStatus('已暂停，点击播放继续收听。');
      this.syncSessionState();
      this.tryHandoff();
    });
    audio.addEventListener('ended', () => {
      if (context !== this.active || context.disposed) return;
      this.showStatus(context.next ? '本次顺序收听已结束。' : '定时收听已结束。点击播放可重新开始。');
      this.syncSessionState();
      this.tryHandoff();
    });
    const ready = () => {
      if (context === this.active) this.updateProgress();
      this.tryHandoff();
    };
    for (const event of ['loadedmetadata', 'canplay', 'seeked']) audio.addEventListener(event, ready);
    audio.addEventListener('seeking', () => { context.lastTime = audio.currentTime; });
    audio.addEventListener('timeupdate', () => {
      const delta = audio.currentTime - context.lastTime;
      if (!audio.seeking && delta > 0) context.heard += delta;
      context.lastTime = audio.currentTime;
      ready();
    });
    audio.addEventListener('ratechange', () => { if (context === this.active) this.syncSessionState(); });
    for (const event of ['waiting', 'stalled']) audio.addEventListener(event, () => {
      if (context === this.active && !audio.paused) this.showStatus('正在准备或缓冲音频，请稍候…');
    });
    audio.addEventListener('error', () => this.failed(context));
    return context;
  }

  private load(context: Playback): void {
    const { audio, token } = context;
    if (!token) {
      audio.src = `/media/model-mr/works/${context.work.id}/audio`;
      this.prepareOrStart(context);
      return;
    }
    const native = Boolean(audio.canPlayType('application/vnd.apple.mpegurl'));
    const aac = typeof MediaSource === 'undefined' || MediaSource.isTypeSupported('audio/mp4; codecs="mp4a.40.2"');
    const query = new URLSearchParams({ start: String(context.work.id), minutes: String(context.minutes),
      offset: context.offset.toFixed(3), seconds: context.limit.toFixed(3), codec: native || aac ? 'aac' : 'mp3',
      next: context.next ? '1' : '0', intro: '0' });
    const url = `/media/model-mr/audio-queues/${token}/index.m3u8?${query}`;
    if (native) {
      audio.src = url;
      this.prepareOrStart(context);
    } else void this.loadHls(context, url);
  }

  private async loadHls(context: Playback, url: string): Promise<void> {
    try {
      const { default: Hls } = await import('hls.js/light');
      if (context.disposed) return;
      if (!Hls.isMSESupported()) throw new Error('media source unavailable');
      const hls = new Hls({ enableWorker: false, startPosition: 0, maxBufferLength: 24, maxMaxBufferLength: 30, backBufferLength: 30 });
      context.hls = hls;
      hls.on(Hls.Events.MEDIA_ATTACHED, () => { if (!context.disposed) { hls.loadSource(url); this.prepareOrStart(context); } });
      hls.on(Hls.Events.ERROR, (_event, data) => { if (data.fatal) this.failed(context); });
      hls.attachMedia(context.audio);
    } catch { this.failed(context); }
  }

  private start(context: Playback): void {
    void context.audio.play().catch(error => {
      if (context.disposed || error?.name === 'AbortError') return;
      this.failed(context);
    });
  }

  private prepareOrStart(context: Playback): void {
    if (this.transition?.target === context) {
      // Prime permission during the tap, then only preload. A phone need not
      // support two simultaneous decoders playing for the preparation period.
      this.start(context);
      context.audio.pause();
    } else this.start(context);
  }

  private originalPosition(context: Playback): number {
    const track = context.track;
    return context.token
      ? Math.max(0, context.audio.currentTime - (track?.start || 0) - (track?.intro_seconds || 0)) + (track?.body_offset ?? context.offset)
      : context.audio.currentTime;
  }

  private changeSettings(resetTimer: boolean, budget?: number, attempts = 0): void {
    const origin = this.active;
    if (!origin) return;
    const previous = this.transition;
    this.cancelTransition();
    const minutes = Number(this.mode.value);
    if (!resetTimer && budget === undefined && origin.next === this.next.checked && origin.minutes === minutes) return;
    const preparingBudget = previous?.target.minutes === minutes
      ? Math.max(0, previous.target.limit - (origin.heard - previous.heard)) : undefined;
    const remaining = budget ?? (resetTimer ? minutes * 60 : preparingBudget ?? (origin.limit ? Math.max(0, origin.limit - origin.audio.currentTime) : minutes * 60));
    if (minutes && remaining <= 0.1) { this.stop(); return; }
    const target = this.create(origin.work, this.next.checked, minutes, remaining, this.originalPosition(origin));
    target.audio.muted = true;
    target.audio.volume = origin.audio.volume;
    target.audio.defaultPlaybackRate = origin.audio.playbackRate;
    target.audio.playbackRate = origin.audio.playbackRate;
    target.audio.hidden = true;
    target.audio.dataset.audioPending = 'true';
    target.audio.setAttribute('aria-hidden', 'true');
    this.slot.append(target.audio);
    const timeout = setTimeout(() => this.failed(target), 60000);
    this.transition = { target, origin, workId: origin.work.id, heard: origin.heard, attempts,
      playRequested: false, readyPlaying: false, wasPaused: origin.audio.paused, timeout };
    this.showStatus(origin.audio.paused ? '正在更新设置，保持暂停…' : '正在准备新设置，当前音频继续播放…');
    this.load(target);
  }

  private tryHandoff(): void {
    const transition = this.transition;
    if (!transition || transition.target.disposed) return;
    const { origin, target } = transition;
    if (origin !== this.active) { this.cancelTransition(); return; }
    let desired = this.originalPosition(origin) - target.offset;
    if (origin.work.id !== transition.workId || desired < -0.05) {
      if (transition.attempts >= 2) { this.failed(target); return; }
      const remaining = target.limit ? Math.max(0, target.limit - (origin.heard - transition.heard)) : 0;
      this.changeSettings(false, remaining, transition.attempts + 1);
      return;
    }
    const audio = target.audio;
    if (audio.readyState < 3 || audio.seeking) return;
    if (Number.isFinite(audio.duration) && desired >= audio.duration - .03) {
      if (origin.audio.ended || origin.audio.paused && Math.abs(desired - audio.duration) < .15) desired = Math.max(0, audio.duration - .04);
      else return;
    }
    if (Math.abs(audio.currentTime - desired) > .08) {
      try { audio.currentTime = Math.max(0, desired); } catch { /* Wait for seekable media. */ }
      return;
    }
    const paused = transition.playRequested ? transition.wasPaused : origin.audio.paused;
    const muted = origin.audio.muted;
    if (!paused && !transition.readyPlaying) {
      if (!transition.playRequested) {
        transition.playRequested = true;
        transition.wasPaused = false;
        this.start(target);
      }
      return;
    }
    clearTimeout(transition.timeout);
    this.transition = null;
    this.active = target;
    audio.defaultPlaybackRate = origin.audio.playbackRate;
    audio.playbackRate = origin.audio.playbackRate;
    audio.controls = true;
    audio.hidden = false;
    delete audio.dataset.audioPending;
    audio.removeAttribute('aria-hidden');
    if (paused) audio.pause();
    this.dispose(origin);
    audio.muted = muted;
    this.renderTitle(target);
    this.setMediaSession();
    this.showStatus(paused ? '设置已更新，保持暂停。点击播放继续收听。' : '设置已更新，继续当前音频。');
    this.updateProgress();
  }

  private failed(context: Playback): void {
    if (context.disposed) return;
    if (this.transition?.target === context) {
      const resume = this.transition.playRequested && !this.transition.wasPaused;
      this.cancelTransition();
      if (this.active) {
        this.next.checked = this.active.next; this.mode.value = String(this.active.minutes);
        const first = this.mode.options[0];
        if (first) first.textContent = this.active.next ? '不定时 · 播放到最新一条' : '不定时 · 单曲循环';
        if (resume && this.active.audio.paused) this.start(this.active);
      }
      this.showStatus('新设置准备失败，保留当前播放。请确认网络后重新选择。', true);
    } else if (context === this.active) {
      context.audio.pause();
      this.showStatus('音频或日期播报暂不可用，请确认网络后重新点作品的“播放音频”。', true);
    }
  }

  private dispose(context: Playback, remove = true): void {
    context.disposed = true;
    context.hls?.destroy();
    context.hls = null;
    context.audio.pause();
    context.audio.removeAttribute('src');
    context.audio.load();
    if (remove) context.audio.remove();
  }

  private cancelTransition(): void {
    const transition = this.transition;
    this.transition = null;
    if (transition) { clearTimeout(transition.timeout); this.dispose(transition.target); }
  }

  pauseForVideo(): void {
    const preparing = Boolean(this.transition);
    this.cancelTransition();
    if (preparing && this.active) {
      this.next.checked = this.active.next;
      this.mode.value = String(this.active.minutes);
      const first = this.mode.options[0];
      if (first) first.textContent = this.active.next ? '不定时 · 播放到最新一条' : '不定时 · 单曲循环';
    }
    this.active?.audio.pause();
    this.releaseMediaSession();
  }

  private stop(): void {
    this.cancelTransition();
    if (this.active) this.dispose(this.active, false);
    this.active = null;
    this.releaseMediaSession();
    this.element.hidden = true;
  }

  private showStatus(message: string, error = false): void {
    this.status.textContent = message;
    this.status.classList.toggle('is-error', error);
  }

  private renderTitle(context: Playback): void {
    const sequence = context.work.sequence_number;
    this.title.textContent = `${context.next ? '顺序收听' : '正在收听'} · ${sequence ? `第 ${String(sequence).padStart(2, '0')} 条 · ` : ''}${context.work.title}`;
  }

  private updateProgress(): void {
    const context = this.active;
    if (!context) return;
    if (context.limit) {
      const remaining = Math.max(0, Math.ceil(context.limit - context.audio.currentTime));
      this.progress.textContent = `剩余 ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`;
    } else this.progress.textContent = context.next ? '按序号递增，播放到最新一条' : '单曲循环';
    if (context.token) void this.readQueueState(context);
    this.syncSessionState();
  }

  private async readQueueState(context: Playback): Promise<void> {
    if (context.reading || Date.now() - context.readAt < 700 || context.disposed) return;
    context.reading = true;
    context.readAt = Date.now();
    try {
      const position = context.audio.currentTime;
      const response = await fetch(`/api/model-mr/audio-queues/${context.token}/state?position=${position.toFixed(3)}`,
        { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) return;
      const state = await response.json() as { current: QueueTrack | null; skipped: number };
      if (context !== this.active || !state.current || Math.abs(position - context.audio.currentTime) > 1) return;
      context.work = state.current;
      context.track = state.current;
      this.renderTitle(context);
      this.tryHandoff();
    } catch { /* Native media continues independently of the status request. */ }
    finally { context.reading = false; }
  }

  private setMediaSession(): void {
    const context = this.active;
    if (!context || !('mediaSession' in navigator)) return;
    try {
      if (typeof MediaMetadata !== 'undefined') navigator.mediaSession.metadata = new MediaMetadata({
        title: context.next ? '模型先生 · 顺序收听（每条语音报读日期和序号）' : context.work.title,
        artist: '模型先生', album: '即时 AI · 作品音频',
      });
      const handlers: Partial<Record<MediaSessionAction, MediaSessionActionHandler>> = {
        play: () => { if (this.active) this.start(this.active); },
        pause: () => this.active?.audio.pause(), stop: () => this.stop(),
        seekbackward: details => this.seek((this.active?.audio.currentTime || 0) - (details.seekOffset || 15)),
        seekforward: details => this.seek((this.active?.audio.currentTime || 0) + (details.seekOffset || 15)),
        seekto: details => this.seek(details.seekTime ?? this.active?.audio.currentTime ?? 0),
      };
      for (const [action, handler] of Object.entries(handlers)) {
        try { navigator.mediaSession.setActionHandler(action as MediaSessionAction, handler!); } catch { /* Unsupported action. */ }
      }
      this.ownsSession = true;
      this.syncSessionState();
    } catch { /* Native controls remain usable. */ }
  }

  private seek(position: number): void {
    const audio = this.active?.audio;
    if (!audio) return;
    const end = Number.isFinite(audio.duration) ? audio.duration : audio.seekable.length ? audio.seekable.end(audio.seekable.length - 1) : 0;
    if (end > 0) audio.currentTime = Math.min(end, Math.max(0, position));
  }

  private syncSessionState(): void {
    if (!this.ownsSession || !this.active) return;
    const audio = this.active.audio;
    try {
      navigator.mediaSession.playbackState = audio.paused ? 'paused' : 'playing';
      if (Number.isFinite(audio.duration) && audio.duration > 0) navigator.mediaSession.setPositionState?.({
        duration: audio.duration, playbackRate: audio.playbackRate, position: Math.min(audio.duration, Math.max(0, audio.currentTime)),
      });
    } catch { /* Some platforms omit position support. */ }
  }

  private releaseMediaSession(): void {
    if (!this.ownsSession) return;
    try {
      for (const action of ['play', 'pause', 'stop', 'seekbackward', 'seekforward', 'seekto'] as MediaSessionAction[]) {
        try { navigator.mediaSession.setActionHandler(action, null); } catch { /* Unsupported action. */ }
      }
      navigator.mediaSession.metadata = null;
      navigator.mediaSession.playbackState = 'none';
      navigator.mediaSession.setPositionState?.();
    } catch { /* Native audio is unaffected. */ }
    this.ownsSession = false;
  }
}
