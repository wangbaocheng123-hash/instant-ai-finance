import type { FinanceItem, TranslationBatchResult } from './types';

interface TranslationHooks {
  enabled: () => boolean;
  find: (id: number) => FinanceItem | null;
  needs: (item: FinanceItem) => boolean;
  translate: (id: number) => Promise<TranslationBatchResult>;
  apply: (result: TranslationBatchResult, title: string) => void;
  busy: (value: boolean) => void;
}

/** A foreground reader, with one submitted headline at a time and no retry. */
export class VisibleTitleTranslation {
  private readonly observer: IntersectionObserver;
  private readonly visible = new Set<Element>();
  private readonly attempted = new Set<string>();
  private timer: number | null = null;
  private watching = false;
  private inFlight = false;
  private blocked = false;

  constructor(private readonly root: HTMLElement, private readonly hooks: TranslationHooks) {
    this.observer = new IntersectionObserver((entries) => {
      if (!this.watching) return;
      entries.forEach((entry) => {
        if (entry.isIntersecting) this.visible.add(entry.target);
        else this.visible.delete(entry.target);
      });
      this.schedule();
    }, { threshold: 0.15 });
  }

  observe(): void {
    this.pause();
    if (!this.foreground()) return;
    this.watching = true;
    this.root.querySelectorAll('[data-headline-id]').forEach((element) => this.observer.observe(element));
  }

  pause(): void {
    this.watching = false;
    this.observer.disconnect();
    this.visible.clear();
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.timer = null;
  }

  retryByOwner(): void {
    this.blocked = false;
    this.attempted.clear();
    this.observe();
  }

  get interrupted(): boolean {
    return this.blocked;
  }

  private foreground(): boolean {
    return document.visibilityState === 'visible' && this.hooks.enabled()
      && this.root.querySelector('.terminal-shell') !== null;
  }

  private schedule(): void {
    if (this.timer !== null || this.inFlight || this.blocked || !this.watching || !this.foreground()) return;
    this.timer = window.setTimeout(() => { this.timer = null; void this.pump(); }, 180);
  }

  private candidate(): FinanceItem | null {
    const overlay = this.root.querySelector('#overlay:not(.hidden)');
    for (const element of this.visible) {
      if (!element.isConnected || (overlay && !overlay.contains(element))) continue;
      const box = element.getBoundingClientRect();
      if (box.width <= 0 || box.height <= 0 || box.bottom <= 0 || box.top >= innerHeight
          || box.right <= 0 || box.left >= innerWidth || !element.getClientRects().length) continue;
      const item = this.hooks.find(Number((element as HTMLElement).dataset.headlineId));
      if (item && this.hooks.needs(item) && !this.attempted.has(item.title)) return item;
    }
    return null;
  }

  private async pump(): Promise<void> {
    if (this.inFlight || this.blocked || !this.watching || !this.foreground()) return;
    const item = this.candidate();
    if (!item) return;
    const title = item.title;
    this.attempted.add(title);
    this.inFlight = true;
    this.hooks.busy(true);
    try {
      const result = await this.hooks.translate(item.id);
      this.hooks.apply(result, title);
      if (result.errors.length || result.quota_exhausted || result.status.enabled === false) this.blocked = true;
    } catch {
      // An unconfirmed paid call must not be automatically repeated.
      this.blocked = true;
    } finally {
      this.inFlight = false;
      this.hooks.busy(false);
      this.schedule();
    }
  }
}
