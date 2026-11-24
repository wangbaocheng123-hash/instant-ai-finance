/** Public source reading only: no background page fetching or browser settings. */
export function publicReadingUrl(value: string): string | null {
  try {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password
        || !url.hostname.includes('.') || /^(?:127\.|10\.|192\.168\.|169\.254\.|0\.)/u.test(url.hostname)
        || /\.(?:local|internal|localhost)$/u.test(url.hostname)
        || /^172\.(?:1[6-9]|2\d|3[01])\./u.test(url.hostname)
        || [...url.searchParams.keys()].some(key => /^(?:token|access_token|id_token|refresh_token|secret|client_secret|password|api_?key|auth|signature)$/iu.test(key))
        || (url.searchParams.has('code') && (/oauth|callback|login/iu.test(url.pathname) || url.searchParams.has('state')))
        || /(?:access_token|id_token|refresh_token|api_key)=/iu.test(url.hash)) return null;
    return url.href;
  } catch { return null; }
}

export function iosChromeUrl(value: string): string | null {
  let url: string;
  try {
    const parsed = new URL(value);
    if (!['https:', 'http:'].includes(parsed.protocol)) return null;
    url = parsed.href;
  } catch { return null; }
  const ios = /iPad|iPhone|iPod/u.test(navigator.userAgent || '')
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  return ios && url ? url.replace(/^https:\/\//u, 'googlechromes://').replace(/^http:\/\//u, 'googlechrome://') : null;
}

export function sourceReadingLinks(value: string, label = '来源网页'): HTMLElement | null {
  const original = publicReadingUrl(value);
  if (!original) return null;
  const translated = new URL('https://translate.google.com/translate');
  translated.searchParams.set('sl', 'auto');
  translated.searchParams.set('tl', 'zh-CN');
  translated.searchParams.set('u', original);
  const box = document.createElement('div'); box.className = 'watch-reading-links';
  const add = (url: string, text: string, mode: string): void => {
    const a = document.createElement('a');
    a.href = iosChromeUrl(url) || url;
    if (!iosChromeUrl(url)) a.target = '_blank';
    a.rel = 'noopener noreferrer'; a.referrerPolicy = 'no-referrer'; a.dataset.reading = mode;
    a.textContent = text;
    box.append(a);
  };
  add(translated.href, `${label} · 中文阅读`, 'chinese');
  add(original, iosChromeUrl(original) ? 'Chrome 原文' : '来源原文', 'original');
  if (iosChromeUrl(original)) {
    const fallback = document.createElement('a');
    fallback.href = original; fallback.target = '_blank'; fallback.rel = 'noopener noreferrer';
    fallback.referrerPolicy = 'no-referrer'; fallback.dataset.reading = 'fallback';
    fallback.textContent = '普通浏览器备用'; box.append(fallback);
  }
  return box;
}
