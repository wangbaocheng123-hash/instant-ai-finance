/* Synthetic APIs and WAV only. This verifies native end-of-media without JS,
 * durable playback across renders, and controls; it is not an iPhone test.
 * Usage: node model-mr-audio-browser.cjs <compiled ESM dir> <artifact dir>
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

function wave(seconds) {
  const rate = 8000, samples = Math.floor(rate * seconds), data = Buffer.alloc(44 + samples * 2);
  data.write('RIFF', 0); data.writeUInt32LE(data.length - 8, 4); data.write('WAVEfmt ', 8);
  data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22);
  data.writeUInt32LE(rate, 24); data.writeUInt32LE(rate * 2, 28);
  data.writeUInt16LE(2, 32); data.writeUInt16LE(16, 34); data.write('data', 36);
  data.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) data.writeInt16LE(Math.round(Math.sin(i / rate * 440 * Math.PI * 2) * 1000), 44 + i * 2);
  return data;
}

const origin = 'http://127.0.0.1:19868';
const compiled = path.resolve(process.argv[2]);
const artifacts = path.resolve(process.argv[3]);
const original = wave(2), timed = wave(.8);
const work = (id, image = false) => ({
  id, title: image ? '合成图文' : `合成作品 ${id}`, description: '', url: '', published_at: '2026-11-10T08:00:00+08:00',
  work_type: image ? 'image' : 'video', media_available: true, video_available: !image,
  video_url: image ? '' : `/media/model-mr/works/${id}/video`, image_urls: [], image_count: 0,
  has_video_text: true, has_interpretation: false, comment_count: 0, keywords: [],
});

(async () => {
  fs.mkdirSync(artifacts, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  let assertions = 0;
  const check = (actual, expected) => { assert.deepEqual(actual, expected); assertions++; };
  try {
    for (const width of [390, 1280]) {
      const page = await browser.newPage({ viewport: { width, height: 844 }, isMobile: width === 390, hasTouch: width === 390 });
      const requests = [], errors = [];
      let failMedia = false, available = true, prepareDelay = 0;
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        window.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
        const originalPlay = HTMLMediaElement.prototype.play;
        window.playGestures = [];
        HTMLMediaElement.prototype.play = function () {
          if (this instanceof HTMLAudioElement) window.playGestures.push(navigator.userActivation.isActive);
          return originalPlay.call(this);
        };
        const canPlay = HTMLMediaElement.prototype.canPlayType;
        HTMLMediaElement.prototype.canPlayType = function(type) {
          return type === 'application/vnd.apple.mpegurl' ? 'probably' : canPlay.call(this, type);
        };
      });
      await page.route(`${origin}/**`, async route => {
        const url = new URL(route.request().url()), p = url.pathname;
        const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
        if (p === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: `<!doctype html><html lang="zh"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/styles.css"><main></main><script type="module">import{ModelMrPanel}from'/ModelMrPanel.js';window.panel=new ModelMrPanel();document.querySelector('main').append(window.panel.element);window.panel.element.hidden=false;await window.panel.refresh();window.ready=true;</script></html>` });
        if (p === '/styles.css') return route.fulfill({ contentType: 'text/css', body: fs.readFileSync(path.join(compiled, 'styles.css')) });
        if (['/ModelMrPanel', '/ModelMrAudioPlayer', '/ModelMrComments', '/api'].some(module => p === module || p === `${module}.js`))
          return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.join(compiled, `${p.slice(1)}${p.endsWith('.js') ? '' : '.js'}`)) });
        if (/^\/media\/model-mr\/works\/\d+\/audio$/.test(p) || p.endsWith('/index.m3u8')) {
          if (p.endsWith('/index.m3u8') && prepareDelay) { const delay=prepareDelay;prepareDelay=0;await new Promise(resolve=>setTimeout(resolve,delay)); }
          requests.push(p + url.search);
          if (failMedia) return route.fulfill({ status: 503, body: 'synthetic unavailable' });
          const data = url.searchParams.has('minutes') ? timed : original;
          const range = route.request().headers().range?.match(/^bytes=(\d+)-(\d*)$/);
          const start = range ? Number(range[1]) : 0;
          const end = range?.[2] ? Math.min(Number(range[2]), data.length - 1) : data.length - 1;
          return route.fulfill({ status: range ? 206 : 200, contentType: 'audio/wav', body: data.subarray(start, end + 1),
            headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${data.length}` } : {}) } });
        }
        if (p.includes('/audio-queues/') && p.endsWith('/state')) return json({ current: null });
        if (p === '/api/model-mr/status') return json({ available, counts: { works: 3, media: 3 }, message: '合成离线' });
        if (p === '/api/model-mr/works') return json({ items: [work(1), work(2), work(3, true)], count: 3, total: 3, offset: 0, has_more: false });
        if (p === '/api/model-mr/learning') return json({ revision: 0, learned_work_ids: [], last_work_id: null });
        if (p === '/api/model-mr/thoughts') return json({ categories: [] });
        if (p === '/api/model-mr/chat/config') return json({ enabled: false, models: [] });
        if (p === '/api/model-mr/processing') return json(null);
        const detail = p.match(/^\/api\/model-mr\/works\/(\d+)$/);
        if (detail) return json({ work: work(Number(detail[1])), video_text: { text: '合成原文', official: true }, transcripts: [], comments: [], comment_total: 0,
          interpretation: { text: '' }, capabilities: { video: true, save_video_text: false, transcribe_video: false, doubao_asr: false } });
        throw new Error(`Unexpected synthetic request: ${p}`);
      });
      await page.goto(origin);
      await page.waitForFunction(() => window.ready);
      const audio = page.locator('.model-audio-player audio');
      const button = id => page.locator(`.model-work-card[data-work-id="${id}"] > .model-work-actions [data-model-action="play-audio"]`);
      check(requests.length, 0);
      check(await button(1).isEnabled(), true);
      check(await button(3).isDisabled(), true);
      await button(1).click();
      await page.waitForFunction(() => { const a = document.querySelector('audio'); return a.currentTime > .1 && !a.paused; });
      check(await audio.evaluate(a => a.loop), true);
      check(await page.evaluate(() => window.playGestures[0]), true);
      check(await page.evaluate(() => navigator.mediaSession.metadata.title), '合成作品 1');
      await audio.evaluate(a => { window.savedAudio = a; window.looped = false; let last = a.currentTime; a.addEventListener('timeupdate', () => { if (a.currentTime < last) window.looped = true; last = a.currentTime; }); });
      await page.waitForFunction(() => window.looped, null, { timeout: 6000 });
      check(await audio.evaluate(a => !a.paused && !a.ended), true);
      await page.evaluate(() => window.panel.refresh());
      check(await audio.evaluate(a => a === window.savedAudio && !a.paused), true);
      await page.locator('[data-model-tab="thoughts"]').click();
      check(await audio.evaluate(a => a === window.savedAudio && !a.paused), true);
      await page.locator('[data-model-tab="works"]').click();
      await page.locator('[data-model-action="open-detail"][data-detail-tab="text"]').first().click();
      await page.locator('[data-model-action="close-detail"]').click();
      check(await audio.evaluate(a => a === window.savedAudio && !a.paused), true);
      available = false;
      await page.evaluate(() => window.panel.refresh());
      check(await audio.evaluate(a => a === window.savedAudio && !a.paused), true);
      available = true;
      await page.evaluate(() => window.panel.refresh());
      await button(2).click();
      await page.waitForFunction(() => { const a = document.querySelector('audio'); return a.src.includes('/2/audio') && a.currentTime > .1 && !a.paused; });
      check(await page.locator('audio').count(), 1);
      check(await page.locator('.model-audio-heading strong').textContent(), '正在收听 · 合成作品 2');
      for (const minutes of ['1', '5', '6', '7', '8', '30', '60']) {
        await page.locator('.model-audio-settings select').selectOption(minutes);
        await page.waitForFunction(() => { const a = document.querySelector('audio'); return !document.querySelector('[data-audio-pending]') && a.currentTime > .05 && !a.paused; });
        check(await audio.evaluate(a => a.loop), false);
        check(requests.at(-1).includes(`minutes=${minutes}`), true);
        check(await page.locator('.model-audio-settings select').evaluate(select => select.options[select.selectedIndex].textContent), minutes === '60' ? '1小时后停止' : `${minutes}分钟后停止`);
        if (minutes === '60') {
          const session = await page.context().newCDPSession(page);
          await session.send('Emulation.setScriptExecutionDisabled', { value: true });
          await new Promise(resolve => setTimeout(resolve, 1400));
          await session.send('Emulation.setScriptExecutionDisabled', { value: false });
          check(await audio.evaluate(a => a.ended && a.paused), true);
          await session.detach();
        }
      }
      await page.locator('.model-audio-settings select').selectOption('0');
      await page.waitForFunction(() => !document.querySelector('[data-audio-pending]'));
      await button(2).click();
      await page.waitForFunction(() => !document.querySelector('audio').paused);
      await page.evaluate(() => {
        const video = document.createElement('video');
        document.body.append(video);
        video.dispatchEvent(new Event('play'));
        video.remove();
      });
      check(await audio.evaluate(a => a.paused), true);
      check(await page.evaluate(() => navigator.mediaSession.metadata), null);
      await button(1).click();
      await page.waitForFunction(() => !document.querySelector('audio').paused);
      prepareDelay=700;
      await page.getByRole('checkbox',{name:'播完自动下一条'}).check();
      await page.evaluate(()=>{
        const video=document.createElement('video');document.body.append(video);video.dispatchEvent(new Event('play'));video.remove();
      });
      check(await audio.evaluate(a=>a.paused),true);
      check(await page.locator('[data-audio-pending]').count(),0);
      check(await page.getByRole('checkbox',{name:'播完自动下一条'}).isChecked(),false);
      await page.screenshot({ path: path.join(artifacts, `audio-${width}.png`), fullPage: true });
      check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
      await page.getByRole('button', { name: '停止收听' }).click();
      check(await audio.evaluate(a => a.paused && !a.hasAttribute('src')), true);
      check(await page.locator('.model-audio-player').isHidden(), true);
      failMedia = true;
      await button(1).click();
      // Select an uncached source; browsers can reuse previously decoded media.
      await page.locator('.model-audio-settings select').selectOption('15');
      try {
        await page.waitForFunction(() => document.querySelector('.model-audio-status').classList.contains('is-error'), null, { timeout: 4000 });
      } catch (error) {
        console.error({ requests: requests.slice(-8), state: await audio.evaluate(a => ({ src: a.src, paused: a.paused, error: a.error?.code, status: document.querySelector('.model-audio-status').outerHTML })) });
        throw error;
      }
      check(await audio.evaluate(a => a.paused), true);
      check(errors, []);
      await page.close();
    }
    process.stdout.write(`PASS ${assertions} audio assertions (390/1280px); finite synthetic WAV end checked with page JS disabled (native branch substituted; real HLS covered separately).\n`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
