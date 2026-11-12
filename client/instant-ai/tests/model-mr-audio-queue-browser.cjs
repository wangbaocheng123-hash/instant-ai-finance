/* Real Chinese speech + synthetic local recordings, no production or paid calls.
 * node model-mr-audio-queue-browser.cjs <compiled dir> <artifact dir>
 * AUDIO_QUEUE_TEST_ENGINE points to the verified Git-outside offline runtime.
 * Chromium uses HLS.js/MP3; ffmpeg independently exercises AAC/native-HLS flow.
 * Neither substitutes for a real iPhone with its screen locked.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');
const compiled = path.resolve(process.argv[2]);
const output = path.resolve(process.argv[3]);
fs.mkdirSync(output, { recursive: true });
const fixture = spawn('python3', ['product/tests/audio_queue_fixture.py'], {
  env: { ...process.env, PYTHONPATH: 'product' }, stdio: ['ignore', 'pipe', 'pipe'],
});
let stderr = '';
fixture.stderr.on('data', data => { stderr = (stderr + data).slice(-8000); });
const originReady = new Promise((resolve, reject) => {
  let text = '';
  fixture.stdout.on('data', chunk => {
    text += chunk;
    if (text.includes('\n')) {
      try { resolve(JSON.parse(text.split('\n')[0]).origin); } catch (error) { reject(error); }
    }
  });
  fixture.on('exit', code => reject(new Error(`fixture exited ${code}: ${stderr}`)));
});

function wave() {
  const result = Buffer.alloc(44 + 32000);
  result.write('RIFF'); result.writeUInt32LE(result.length - 8, 4); result.write('WAVEfmt ', 8);
  result.writeUInt32LE(16, 16); result.writeUInt16LE(1, 20); result.writeUInt16LE(1, 22);
  result.writeUInt32LE(8000, 24); result.writeUInt32LE(16000, 28); result.writeUInt16LE(2, 32);
  result.writeUInt16LE(16, 34); result.write('data', 36); result.writeUInt32LE(32000, 40);
  for (let i = 0; i < 16000; i++) result.writeInt16LE(Math.round(Math.sin(i / 8000 * 440 * 2 * Math.PI) * 1000), 44 + i * 2);
  return result;
}
const plain = wave();
const works = [400, 30, 200, 10].map((id, index) => ({
  id, title: `合成作品 ${id}`, description: '', url: '', sequence_number: 4 - index,
  published_at: '2026-11-13T08:00:00+08:00', work_type: id === 200 ? 'image' : 'video',
  media_available: id !== 200, video_available: id !== 200, video_url: '', image_urls: [], image_count: 0,
  has_video_text: true, has_interpretation: false, comment_count: 0, keywords: [],
}));

(async () => {
  const origin = await originReady;
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
  let count = 0;
  const check = (value, expected) => { assert.deepEqual(value, expected); count++; };
  try {
    for (const width of process.env.AUDIO_QUEUE_NATIVE_ONLY ? [] : [390, 1280]) {
      const page = await browser.newPage({ viewport: { width, height: 844 }, isMobile: width === 390, hasTouch: width === 390 });
      const errors = [], urls = [], seen = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('response', async response => {
        if (response.url().includes('/state?')) {
          try { const state = await response.json(); if (state.current && seen.at(-1) !== state.current.id) seen.push(state.current.id); } catch {}
        }
      });
      await page.addInitScript(() => {
        window.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
      });
      await page.route(`${origin}/**`, async route => {
        const url = new URL(route.request().url()), p = url.pathname;
        const json = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
        if (p.startsWith('/media/model-mr/audio-queues/') || p.startsWith('/api/model-mr/audio-queues/')) {
          urls.push(url.href); return route.continue();
        }
        if (p === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: `<!doctype html><html lang="zh"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/styles.css"><script type="importmap">{"imports":{"hls.js/light":"/hls.mjs"}}</script><main></main><script type="module">import{ModelMrPanel}from'/ModelMrPanel.js';window.panel=new ModelMrPanel();document.querySelector('main').append(window.panel.element);window.panel.element.hidden=false;await window.panel.refresh();window.ready=true;</script></html>` });
        if (p === '/hls.mjs') return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync('client/instant-ai/node_modules/hls.js/dist/hls.light.mjs') });
        if (p === '/styles.css') return route.fulfill({ contentType: 'text/css', body: fs.readFileSync(path.join(compiled, 'styles.css')) });
        if (['/ModelMrPanel', '/ModelMrAudioPlayer', '/ModelMrComments', '/api'].some(module => p === module || p === module + '.js'))
          return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.join(compiled, `${p.slice(1)}${p.endsWith('.js') ? '' : '.js'}`)) });
        if (/^\/media\/model-mr\/works\/\d+\/audio$/.test(p)) return route.fulfill({ contentType: 'audio/wav', body: plain });
        if (p.endsWith('/status')) return json({ available: true, counts: { works: 4, media: 3 } });
        if (p === '/api/model-mr/works') return json({ items: works, count: 4, total: 4, offset: 0, has_more: false });
        if (p.endsWith('/learning')) return json({ revision: 0, learned_work_ids: [], last_work_id: null });
        if (p.endsWith('/thoughts')) return json({ categories: [] });
        if (p.endsWith('/chat/config')) return json({ enabled: false, models: [] });
        if (p.endsWith('/processing')) return json(null);
        throw new Error(`Unexpected synthetic request: ${p}`);
      });
      await page.goto(origin);
      await page.waitForFunction(() => window.ready);
      const audio = page.locator('audio');
      await page.locator('.model-work-card[data-work-id="10"] [data-model-action="play-audio"]').click();
      await page.waitForFunction(() => document.querySelector('audio').currentTime > .15 && !document.querySelector('audio').paused);
      await audio.evaluate(a => { window.savedAudio = a; });
      await page.getByRole('checkbox', { name: '播完自动下一条' }).check();
      try {
        await page.waitForFunction(() => document.querySelector('.model-audio-heading strong').textContent.includes('第 03 条'), null, { timeout: 30000 });
      } catch (error) {
        console.error({ width, seen, urls: urls.slice(-12), player: await audio.evaluate(a => ({ current: a.currentTime, duration: a.duration, paused: a.paused, mediaError: a.error?.code, status: document.querySelector('.model-audio-status').textContent })) });
        throw error;
      }
      check(await audio.evaluate(a => a === window.savedAudio && !a.paused && !a.loop), true);
      const initial = new URL(urls.find(url => url.includes('/index.m3u8')));
      check(initial.searchParams.get('codec'), 'mp3');
      check(Number(initial.searchParams.get('offset')) > 0, true);
      await page.evaluate(() => window.panel.refresh());
      check(await audio.evaluate(a => a === window.savedAudio && !a.paused), true);
      await page.screenshot({ path: path.join(output, `sequence-${width}.png`), fullPage: true });
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.waitForFunction(() => document.querySelector('audio').ended, null, { timeout: 30000 });
      check(seen, [10, 30, 400]);
      check(await page.locator('audio').count(), 1);
      check(await page.locator('.model-audio-status').textContent(), '本次顺序收听已结束。');
      await page.getByRole('checkbox', { name: '播完自动下一条' }).uncheck();
      await page.waitForFunction(() => { const a = document.querySelector('audio'); return a.loop && !a.paused && a.src.includes('/400/audio'); });
      check(await page.locator('.model-audio-settings select').inputValue(), '0');
      await page.getByRole('button', { name: '停止收听' }).click();
      check(await audio.evaluate(a => a.paused && !a.hasAttribute('src')), true);
      check(errors, []);
      await page.close();
    }
    const nativePage = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await nativePage.addInitScript(() => {
      const supported = HTMLMediaElement.prototype.canPlayType;
      HTMLMediaElement.prototype.canPlayType = function(type) {
        return type === 'application/vnd.apple.mpegurl' ? 'probably' : supported.call(this, type);
      };
      window.nativeCalls = [];
      HTMLMediaElement.prototype.play = function() {
        window.nativeCalls.push({ src: this.src, gesture: navigator.userActivation.isActive, loop: this.loop });
        return Promise.resolve();
      };
    });
    await nativePage.route(`${origin}/**`, async route => {
      const p = new URL(route.request().url()).pathname;
      if (p === '/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><button id="start">Start</button><main></main><script type="module">import{ModelMrAudioPlayer}from"/ModelMrAudioPlayer.js";window.player=new ModelMrAudioPlayer();document.querySelector("main").append(player.element);player.element.hidden=false;document.querySelector("#start").onclick=()=>player.play({id:10,title:"Synthetic native HLS"});window.ready=true;</script>' });
      if (p === '/ModelMrAudioPlayer.js') return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.join(compiled, 'ModelMrAudioPlayer.js')) });
      return route.abort();
    });
    await nativePage.goto(origin);
    await nativePage.waitForFunction(() => window.ready);
    await nativePage.getByRole('checkbox', { name: '播完自动下一条' }).check();
    await nativePage.locator('#start').click();
    const call = await nativePage.evaluate(() => window.nativeCalls.at(-1));
    check(call.gesture, true);
    check(call.loop, false);
    check(new URL(call.src).searchParams.get('codec'), 'aac');
    check(new URL(call.src).pathname.endsWith('/index.m3u8'), true);
    await nativePage.close();
    if (!process.env.AUDIO_QUEUE_NATIVE_ONLY) {
    const token = 'c'.repeat(32);
    const file = path.join(output, 'native-aac-no-page-script.wav');
    await new Promise((resolve, reject) => {
      const process = spawn('ffmpeg', ['-v', 'error', '-i', `${origin}/media/model-mr/audio-queues/${token}/index.m3u8?start=10&codec=aac`, '-vn', '-c:a', 'pcm_s16le', '-y', file]);
      let diagnostic = '';
      const timeout = setTimeout(() => { process.kill(); reject(new Error('native AAC protocol timeout')); }, 45000);
      process.stderr.on('data', chunk => { diagnostic += chunk; });
      process.on('exit', code => { clearTimeout(timeout); code === 0 ? resolve() : reject(new Error(diagnostic)); });
    });
    check(fs.statSync(file).size > 200000, true);
    }
    process.stdout.write(process.env.AUDIO_QUEUE_NATIVE_ONLY
      ? `PASS ${count} native-HLS source/gesture assertions (play method stubbed; not an iPhone playback test).\n`
      : `PASS ${count} assertions; actual Chinese speech and 3 recordings advanced in Chromium at 390/1280px; AAC HLS decoded without page JavaScript; native source/gesture branch checked.\n`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error, stderr); process.exitCode = 1; }).finally(() => fixture.kill('SIGINT'));
