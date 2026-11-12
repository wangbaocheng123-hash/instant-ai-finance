import type { ModelMrWork } from './types';
/*! hls.js 1.7.4 — Apache-2.0; full license and notices: /hls-js-license.txt */
type AudioWork = Pick<ModelMrWork, 'id' | 'title'> & Partial<Pick<ModelMrWork, 'published_at' | 'sequence_number'>>;

/** One durable native audio element, outside the refreshed works/detail DOM. */
export class ModelMrAudioPlayer {
  readonly element = document.createElement('section');
  private readonly audio = document.createElement('audio');
  private readonly title = document.createElement('strong');
  private readonly status = document.createElement('p');
  private readonly progress = document.createElement('output');
  private readonly mode = document.createElement('select');
  private readonly next = document.createElement('input');
  private work: AudioWork | null = null;
  private queueToken: string | null = null;
  private queueStatePending: string | null = null;
  private queueStateReadAt = 0;
  private hls: import('hls.js').default | null = null;
  private preparingHls = false;
  private request = 0;
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
    this.audio.controls = true;
    this.audio.preload = 'none';
    this.audio.setAttribute('aria-label', '作品音频');
    const settings = document.createElement('label');
    settings.className = 'model-audio-settings';
    settings.append('播放模式');
    this.mode.setAttribute('aria-label', '音频循环与定时停止');
    for (const [value, label] of [['0', '一直循环'], ['15', '15分钟后停止'], ['30', '30分钟后停止'], ['60', '60分钟后停止'], ['90', '90分钟后停止']]) {
      const option = document.createElement('option');
      option.value = value!;
      option.textContent = label!;
      this.mode.append(option);
    }
    this.mode.addEventListener('change', () => {
      // A direct gesture starts native playback even while the server prepares
      // the resource. Awaiting a preparation API first loses iOS user activation.
      if (this.work) this.play(this.work);
    });
    settings.append(this.mode, this.progress);
    const continuous = document.createElement('label');
    continuous.className = 'model-audio-continuous';
    this.next.type = 'checkbox';
    this.next.setAttribute('aria-label', '播完自动下一条');
    continuous.append(this.next, '播完自动下一条（按序号递增）');
    this.next.addEventListener('change', () => {
      const first = this.mode.options[0];
      if (first) first.textContent = this.next.checked ? '顺序播放到最新一条' : '一直循环';
      // Continue the current recording from its current position when enabling
      // the queue, then let the native HLS media timeline advance to the next.
      const offset = this.next.checked && !this.queueToken ? Math.max(0, this.audio.currentTime || 0) : 0;
      if (this.work) this.play(this.work, offset);
    });
    this.status.className = 'model-audio-status';
    const hint = document.createElement('p');
    hint.className = 'model-audio-hint';
    hint.textContent = '顺序播放时，每条先报读发布日期和序号，播到最新一条后停止；图文等无本地音轨的作品会跳过。定时按播放进度，暂停不计时；更换作品或定时设置会重新计时。请保持联网。';
    this.element.append(heading, this.audio, settings, continuous, this.status, hint);
    this.audio.addEventListener('play', () => {
      document.querySelectorAll('video').forEach(video => video.pause());
      this.setMediaSession();
    });
    this.audio.addEventListener('playing', () => {
      this.showStatus('正在播放，可锁屏收听。');
      this.syncSessionState();
    });
    this.audio.addEventListener('pause', () => {
      if (this.audio.paused && !this.preparingHls && !this.audio.ended && this.work && !this.audio.error && !this.status.classList.contains('is-error')) {
        this.showStatus('已暂停，点击播放继续收听。');
      }
      this.syncSessionState();
    });
    this.audio.addEventListener('ended', () => {
      this.showStatus(this.next.checked ? '本次顺序收听已结束。' : '定时收听已结束。点击播放可重新开始。');
      this.syncSessionState();
    });
    const buffering = () => {
      if (!this.audio.paused) this.showStatus('正在准备或缓冲音频，首次播放请稍候…');
    };
    this.audio.addEventListener('waiting', buffering);
    this.audio.addEventListener('stalled', buffering);
    this.audio.addEventListener('error', () => {
      if (!this.preparingHls) this.showStatus('音频暂不可用，请确认网络后重新点作品的“播放音频”。缺少本地视频或音轨的作品暂不能收听。', true);
    });
    this.audio.addEventListener('loadedmetadata', () => this.updateProgress());
    this.audio.addEventListener('timeupdate', () => this.updateProgress());
    this.audio.addEventListener('ratechange', () => this.syncSessionState());
    document.addEventListener('play', event => {
      if (event.target instanceof HTMLVideoElement && this.ownsSession) this.pauseForVideo();
    }, true);
  }

  play(work: AudioWork, offset = 0): void {
    const request = ++this.request;
    this.hls?.destroy();
    this.hls = null;
    this.preparingHls = false;
    this.queueToken = null;
    this.queueStateReadAt = 0;
    this.work = { id: work.id, title: work.title, published_at: work.published_at, sequence_number: work.sequence_number };
    this.title.textContent = `正在收听 · ${work.title}`;
    this.element.hidden = false;
    const minutes = Number(this.mode.value);
    this.audio.loop = !this.next.checked && minutes === 0;
    if (this.next.checked) {
      const token = Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
      this.queueToken = token;
      const native = Boolean(this.audio.canPlayType('application/vnd.apple.mpegurl'));
      const aac = typeof MediaSource === 'undefined' || MediaSource.isTypeSupported('audio/mp4; codecs="mp4a.40.2"');
      const codec = native || aac ? 'aac' : 'mp3';
      const query = new URLSearchParams({ start: String(work.id), minutes: String(minutes), offset: offset.toFixed(3), codec });
      const url = `/media/model-mr/audio-queues/${token}/index.m3u8?${query}`;
      this.title.textContent = `顺序收听 · ${work.title}`;
      this.progress.textContent = minutes ? `本次收听 ${minutes} 分钟` : '按序号递增，播放到最新一条';
      this.showStatus('正在准备顺序音频和中文报读，首次使用请稍候…');
      this.setMediaSession();
      if (native) {
        this.audio.src = url;
        this.startPlayback(request);
      } else {
        this.audio.pause();
        this.preparingHls = true;
        void this.startHls(url, request);
      }
      return;
    }
    this.audio.src = `/media/model-mr/works/${work.id}/audio${minutes ? `?minutes=${minutes}` : ''}`;
    this.progress.textContent = minutes ? `本次收听 ${minutes} 分钟` : '单曲循环';
    this.showStatus('正在准备音频，首次播放请稍候…');
    this.setMediaSession();
    // Keep this synchronous with the tap, including source assignment.
    this.startPlayback(request);
  }

  private startPlayback(request: number): void {
    void this.audio.play().catch(error => {
      if (request !== this.request || error?.name === 'AbortError') return;
      this.showStatus('尚未开始播放，请点击音频播放器上的播放键；若仍失败，可重新点作品的“播放音频”。', true);
    });
  }

  private async startHls(url: string, request: number): Promise<void> {
    try {
      const { default: Hls } = await import('hls.js/light');
      if (request !== this.request) return;
      if (!Hls.isMSESupported()) throw new Error('media source unavailable');
      const hls = new Hls({ enableWorker: false, startPosition: 0, maxBufferLength: 24,
        maxMaxBufferLength: 30, backBufferLength: 30 });
      this.hls = hls;
      hls.on(Hls.Events.MEDIA_ATTACHED, () => {
        if (request !== this.request) return;
        hls.loadSource(url);
        this.preparingHls = false;
        this.startPlayback(request);
      });
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (request !== this.request || !data.fatal) return;
        this.audio.pause();
        this.showStatus('顺序音频或中文报读暂不可用，请重试；也可取消“自动下一条”继续单曲收听。', true);
      });
      hls.attachMedia(this.audio);
    } catch {
      if (request !== this.request) return;
      this.preparingHls = false;
      this.showStatus('顺序播放器暂未能启动，请确认网络并刷新，或取消“自动下一条”收听单曲。', true);
    }
  }

  pauseForVideo(): void {
    this.audio.pause();
    this.releaseMediaSession();
  }

  private stop(): void {
    this.request++;
    this.queueToken = null;
    this.hls?.destroy();
    this.hls = null;
    this.preparingHls = false;
    this.work = null;
    this.audio.pause();
    this.audio.removeAttribute('src');
    this.audio.load();
    this.releaseMediaSession();
    this.element.hidden = true;
  }

  private showStatus(message: string, error = false): void {
    this.status.textContent = message;
    this.status.classList.toggle('is-error', error);
  }

  private updateProgress(): void {
    if (this.queueToken) {
      const seconds = Number(this.mode.value) * 60;
      const end = Number.isFinite(this.audio.duration) ? Math.min(seconds || Infinity, this.audio.duration) : seconds;
      const remaining = Math.max(0, Math.ceil(end - this.audio.currentTime));
      this.progress.textContent = end ? `剩余 ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}` : '按序号递增，播放到最新一条';
      void this.readQueueState();
      this.syncSessionState();
      return;
    }
    if (this.audio.loop) this.progress.textContent = '单曲循环';
    else if (Number.isFinite(this.audio.duration)) {
      const remaining = Math.max(0, Math.ceil(this.audio.duration - this.audio.currentTime));
      this.progress.textContent = `剩余 ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`;
    }
    this.syncSessionState();
  }

  private async readQueueState(): Promise<void> {
    const token = this.queueToken;
    if (!token || this.queueStatePending === token || Date.now() - this.queueStateReadAt < 1000) return;
    this.queueStatePending = token;
    this.queueStateReadAt = Date.now();
    try {
      const response = await fetch(`/api/model-mr/audio-queues/${token}/state?position=${this.audio.currentTime.toFixed(3)}`,
        { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) return;
      const state = await response.json() as { current: AudioWork | null; skipped: number };
      if (token !== this.queueToken || !state.current) return;
      this.work = state.current;
      const sequence = state.current.sequence_number;
      this.title.textContent = `顺序收听 · ${sequence ? `第 ${String(sequence).padStart(2, '0')} 条 · ` : ''}${state.current.title}`;
      if (state.skipped && !this.audio.paused) this.showStatus(`正在顺序收听；已跳过 ${state.skipped} 条音轨不可用的作品。`);
    } catch { /* HLS playback continues independently of the page's status read. */ }
    finally { if (this.queueStatePending === token) this.queueStatePending = null; }
  }

  private setMediaSession(): void {
    if (!this.work || !('mediaSession' in navigator)) return;
    try {
      if (typeof MediaMetadata !== 'undefined') navigator.mediaSession.metadata = new MediaMetadata({
        title: this.queueToken ? '模型先生 · 顺序收听（每条语音报读日期和序号）' : this.work.title,
        artist: '模型先生', album: '即时 AI · 作品音频',
      });
      const handlers: Partial<Record<MediaSessionAction, MediaSessionActionHandler>> = {
        play: () => { void this.audio.play().catch(() => this.showStatus('请回到页面点击播放。', true)); },
        pause: () => this.audio.pause(),
        stop: () => this.stop(),
        seekbackward: details => this.seek(this.audio.currentTime - (details.seekOffset || 15)),
        seekforward: details => this.seek(this.audio.currentTime + (details.seekOffset || 15)),
        seekto: details => this.seek(details.seekTime ?? this.audio.currentTime),
      };
      for (const [action, handler] of Object.entries(handlers)) {
        try { navigator.mediaSession.setActionHandler(action as MediaSessionAction, handler!); } catch { /* Unsupported platform action. */ }
      }
      this.ownsSession = true;
      this.syncSessionState();
    } catch { /* Native audio remains usable when Media Session is unavailable. */ }
  }

  private seek(position: number): void {
    const end = Number.isFinite(this.audio.duration) ? this.audio.duration
      : this.audio.seekable.length ? this.audio.seekable.end(this.audio.seekable.length - 1) : 0;
    if (end > 0) this.audio.currentTime = Math.min(end, Math.max(0, position));
  }

  private syncSessionState(): void {
    if (!this.ownsSession) return;
    try {
      navigator.mediaSession.playbackState = this.audio.paused ? 'paused' : 'playing';
      if (Number.isFinite(this.audio.duration) && this.audio.duration > 0) navigator.mediaSession.setPositionState?.({
        duration: this.audio.duration, playbackRate: this.audio.playbackRate,
        position: Math.min(this.audio.duration, Math.max(0, this.audio.currentTime)),
      });
    } catch { /* Older browsers may omit position support. */ }
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
