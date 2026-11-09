import type { ModelMrWork } from './types';

/** One durable native audio element, outside the refreshed works/detail DOM. */
export class ModelMrAudioPlayer {
  readonly element = document.createElement('section');
  private readonly audio = document.createElement('audio');
  private readonly title = document.createElement('strong');
  private readonly status = document.createElement('p');
  private readonly progress = document.createElement('output');
  private readonly mode = document.createElement('select');
  private work: Pick<ModelMrWork, 'id' | 'title'> | null = null;
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
    this.status.className = 'model-audio-status';
    const hint = document.createElement('p');
    hint.className = 'model-audio-hint';
    hint.textContent = '先点播放，再锁屏收听。定时按播放进度计算，暂停不计时；切换模式或作品会从头播放、重新计时。请保持联网。';
    this.element.append(heading, this.audio, settings, this.status, hint);
    this.audio.addEventListener('play', () => {
      document.querySelectorAll('video').forEach(video => video.pause());
      this.setMediaSession();
    });
    this.audio.addEventListener('playing', () => {
      this.showStatus('正在播放，可锁屏收听。');
      this.syncSessionState();
    });
    this.audio.addEventListener('pause', () => {
      if (!this.audio.ended && this.work && !this.audio.error && !this.status.classList.contains('is-error')) {
        this.showStatus('已暂停，点击播放继续收听。');
      }
      this.syncSessionState();
    });
    this.audio.addEventListener('ended', () => {
      this.showStatus('定时收听已结束。点击播放可重新开始。');
      this.syncSessionState();
    });
    const buffering = () => {
      if (!this.audio.paused) this.showStatus('正在准备或缓冲音频，首次播放请稍候…');
    };
    this.audio.addEventListener('waiting', buffering);
    this.audio.addEventListener('stalled', buffering);
    this.audio.addEventListener('error', () => this.showStatus('音频暂不可用，请确认网络后重新点作品的“播放音频”。缺少本地视频或音轨的作品暂不能收听。', true));
    this.audio.addEventListener('loadedmetadata', () => this.updateProgress());
    this.audio.addEventListener('timeupdate', () => this.updateProgress());
    this.audio.addEventListener('ratechange', () => this.syncSessionState());
    document.addEventListener('play', event => {
      if (event.target instanceof HTMLVideoElement && this.ownsSession) this.pauseForVideo();
    }, true);
  }

  play(work: Pick<ModelMrWork, 'id' | 'title'>): void {
    const request = ++this.request;
    this.work = { id: work.id, title: work.title };
    this.title.textContent = `正在收听 · ${work.title}`;
    this.element.hidden = false;
    const minutes = Number(this.mode.value);
    this.audio.loop = minutes === 0;
    this.audio.src = `/media/model-mr/works/${work.id}/audio${minutes ? `?minutes=${minutes}` : ''}`;
    this.progress.textContent = minutes ? `本次收听 ${minutes} 分钟` : '单曲循环';
    this.showStatus('正在准备音频，首次播放请稍候…');
    this.setMediaSession();
    // Keep this synchronous with the tap, including source assignment.
    void this.audio.play().catch(error => {
      if (request !== this.request || error?.name === 'AbortError') return;
      this.showStatus('尚未开始播放，请点击音频播放器上的播放键；若仍失败，可重新点作品的“播放音频”。', true);
    });
  }

  pauseForVideo(): void {
    this.audio.pause();
    this.releaseMediaSession();
  }

  private stop(): void {
    this.request++;
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
    if (this.audio.loop) this.progress.textContent = '单曲循环';
    else if (Number.isFinite(this.audio.duration)) {
      const remaining = Math.max(0, Math.ceil(this.audio.duration - this.audio.currentTime));
      this.progress.textContent = `剩余 ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`;
    }
    this.syncSessionState();
  }

  private setMediaSession(): void {
    if (!this.work || !('mediaSession' in navigator)) return;
    try {
      if (typeof MediaMetadata !== 'undefined') navigator.mediaSession.metadata = new MediaMetadata({
        title: this.work.title, artist: '模型先生', album: '即时 AI · 作品音频',
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
    if (Number.isFinite(this.audio.duration)) this.audio.currentTime = Math.min(this.audio.duration, Math.max(0, position));
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
