/* Synthetic APIs and local media only; never reads production or calls AI.
 * Usage: node model-mr-learning-browser.cjs <compiled-esm-dir> <synthetic-video>
 * Compile ModelMrPanel.ts/api.ts/ModelMrComments.ts to ES2020 in a Git-outside
 * directory. Existing Playwright/Chromium can be selected using NODE_PATH and
 * CHROMIUM_EXECUTABLE. Actual local WebM playback covers DOM lifetime, not
 * iPhone native fullscreen, mobile transport, or production acceptance.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const origin = 'http://127.0.0.1:19864';
const action = name => `[data-model-action="${name}"]`;
const mark = id => `${action('toggle-learned')}[data-work-id="${id}"]`;
const card = id => `.model-work-card[data-work-id="${id}"]`;
const clone = value => JSON.parse(JSON.stringify(value));

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function fixture() {
  return {
    ids: Array.from({ length: 750 }, (_, index) => 750 - index),
    learning: { revision: 0, learned_work_ids: [], last_work_id: null },
    markOrder: [],
    requests: [], unexpected: [], failedWrites: 0, failedReads: 0,
    holdWrite: null, holdRead: null, holdWorks: null,
  };
}

function setLearned(state, id, learned = true) {
  const before = state.markOrder.includes(id);
  if (before === learned) return;
  state.markOrder = state.markOrder.filter(value => value !== id);
  if (learned) state.markOrder.push(id);
  state.learning = {
    revision: state.learning.revision + 1,
    learned_work_ids: [...state.markOrder].sort((a, b) => a - b),
    last_work_id: state.markOrder.at(-1) ?? null,
  };
}

function work(id) {
  return {
    id, title: `合成学习作品 ${id}`, description: '', work_type: 'video', title_source: 'source',
    published_at: new Date(Date.UTC(2026, 9, 11) - (750 - id) * 86400000).toISOString(),
    captured_at: '2026-10-11T08:00:00+08:00', media_available: true, video_available: true,
    video_url: `/media/model-mr/works/${id}/video`, image_urls: [], image_count: 0,
    has_video_text: true, has_interpretation: false, keywords: [], keyword_revision: 'r1',
    keyword_info: { categories: {}, keywords: [] }, comment_count: 0,
  };
}

async function mount(browser, compiled, media, width, state = fixture()) {
  const page = await browser.newPage({ viewport: { width, height: 844 }, isMobile: width === 390, hasTouch: width === 390 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    // Trigger refreshes and pagination deliberately, without timer/observer races.
    window.setInterval = () => 0;
    window.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    const scroll = Element.prototype.scrollIntoView;
    window.learningScrolls = [];
    Element.prototype.scrollIntoView = function (...args) {
      if (this.matches('.model-work-card')) window.learningScrolls.push(Number(this.dataset.workId));
      return scroll.apply(this, args);
    };
  });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const method = route.request().method();
    const json = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (url.origin !== origin) {
      state.unexpected.push(`${method} ${url.origin}${p}`);
      return route.abort();
    }
    if (p === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/styles.css"><body><main id="root"></main><script type="module">import {ModelMrPanel} from '/ModelMrPanel.js';window.reopen=async()=>{window.panel=new ModelMrPanel();document.querySelector('#root').replaceChildren(window.panel.element);window.panel.element.hidden=false;await window.panel.refresh();};await window.reopen();window.ready=true;</script></body></html>` });
    if (p === '/styles.css') return route.fulfill({ contentType: 'text/css', body: fs.readFileSync(path.resolve(__dirname, '../src/instant-ai/styles.css'), 'utf8') });
    if (['/ModelMrPanel.js', '/ModelMrComments.js', '/api.js', '/ModelMrComments', '/api'].includes(p))
      return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.join(compiled, `${p.slice(1)}${p.endsWith('.js') ? '' : '.js'}`), 'utf8') });
    if (p.startsWith('/media/')) {
      state.requests.push({ p, method });
      const range = route.request().headers().range?.match(/^bytes=(\d+)-(\d*)$/);
      const start = range ? Number(range[1]) : 0;
      const end = range?.[2] ? Math.min(Number(range[2]), media.length - 1) : media.length - 1;
      return route.fulfill({ status: range ? 206 : 200, contentType: 'video/webm', headers: {
        'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${media.length}` } : {}),
      }, body: media.subarray(start, end + 1) });
    }
    state.requests.push({ p, method, query: Object.fromEntries(url.searchParams) });
    if (p === '/api/model-mr/status') return json({ available: true, counts: { works: state.ids.length, media: state.ids.length } });
    if (p === '/api/model-mr/thoughts') return json({ categories: [], count: 0 });
    if (p === '/api/model-mr/chat/config') return json({ enabled: false, models: [] });
    if (p === '/api/model-mr/processing') return json(null);
    if (p === '/api/model-mr/learning') {
      if (state.failedReads > 0) { state.failedReads--; return json({ error: '合成学习记录读取失败' }, 503); }
      const snapshot = clone(state.learning);
      const gate = state.holdRead;
      state.holdRead = null;
      if (gate) await gate.promise;
      return json(snapshot);
    }
    const save = p.match(/^\/api\/model-mr\/works\/(\d+)\/learned$/);
    if (save && method === 'POST') {
      const payload = route.request().postDataJSON();
      state.requests.at(-1).payload = payload;
      assert.equal(route.request().headers()['x-instant-ai'], '1', 'learning writes retain the existing owner-write header');
      const gate = state.holdWrite;
      state.holdWrite = null;
      if (gate) await gate.promise;
      if (state.failedWrites > 0) { state.failedWrites--; return json({ error: '合成保存失败，请重试' }, 503); }
      if (payload.expected_revision !== state.learning.revision) return json({ error: '学习记录已在另一页面更新，请刷新后重试', code: 'learning_revision_conflict' }, 409);
      assert.equal(typeof payload.learned, 'boolean');
      setLearned(state, Number(save[1]), payload.learned);
      return json(state.learning);
    }
    if (p === '/api/model-mr/works') {
      const limit = Math.max(1, Math.min(500, Number(url.searchParams.get('limit') || 24)));
      const around = Number(url.searchParams.get('around_id') || 0);
      const index = around ? state.ids.indexOf(around) : -1;
      if (around && index < 0) return json({ error: '上次学习的作品暂不可用' }, 404);
      const relative = url.searchParams.has('relative_offset') ? Number(url.searchParams.get('relative_offset')) : -Math.floor(limit / 2);
      const offset = around ? Math.max(0, index + relative) : Number(url.searchParams.get('offset') || 0);
      const items = state.ids.slice(offset, offset + limit).map(work);
      const snapshot = { items, count: items.length, total: state.ids.length, offset, has_more: offset + items.length < state.ids.length };
      const gate = state.holdWorks;
      state.holdWorks = null;
      if (gate) await gate.promise;
      return json(snapshot);
    }
    const detail = p.match(/^\/api\/model-mr\/works\/(\d+)$/);
    if (detail) return json({ work: work(Number(detail[1])), video_text: { text: '合成原文，不调用任何模型。', official: true },
      interpretation: { text: '' }, transcripts: [], comments: [], comment_total: 0,
      stock_mentions: { items: [], stock_count: 0, total_comments: 0, uncertain: [] }, capabilities: { video: true } });
    state.unexpected.push(`${method} ${p}`);
    return json({ error: `Unexpected synthetic request: ${method} ${p}` }, 500);
  });
  await page.goto(origin);
  await page.waitForFunction(() => window.ready);
  return { page, state, errors, compiled, width };
}

async function waitRequests(state, predicate, previous) {
  const deadline = Date.now() + 5000;
  while (state.requests.filter(predicate).length === previous) {
    assert.ok(Date.now() < deadline, 'expected synthetic request must start');
    await new Promise(done => setTimeout(done, 5));
  }
}

async function waitPressed(page, id, pressed) {
  await page.waitForFunction(({ id, pressed }) => {
    const button = document.querySelector(`[data-model-action="toggle-learned"][data-work-id="${id}"]`);
    return button?.getAttribute('aria-pressed') === String(pressed) && !button.disabled;
  }, { id, pressed });
}

async function toggle(page, id, pressed) {
  await page.locator(mark(id)).click();
  await waitPressed(page, id, pressed);
}

async function renderedIds(page) {
  return page.locator('.model-work-list-full > .model-work-card').evaluateAll(cards => cards.map(item => Number(item.dataset.workId)));
}

async function emptyAndMark(session) {
  const { page, state } = session;
  assert.equal(await page.locator(action('learning-return')).isDisabled(), true);
  assert.equal(await page.locator(mark(750)).getAttribute('aria-pressed'), 'false');
  assert.equal(await page.locator('video').count(), 0);
  await toggle(page, 750, true);
  await toggle(page, 749, true);
  assert.equal(state.learning.last_work_id, 749, 'last learned means latest mark, not latest video date');
  await toggle(page, 749, false);
  assert.equal(state.learning.last_work_id, 750, 'unmarking latest returns to previous remaining mark');
  await page.locator(action('learning-return')).click();
  await page.locator(`${card(750)}.is-learning-target`).waitFor();
  await toggle(page, 750, false);
  assert.equal(state.learning.last_work_id, null);
  assert.equal(await page.locator(action('learning-return')).isDisabled(), true);
  assert.equal(await page.locator('video').count(), 0, 'marks/jump must not autoplay or mount media');
}

async function persistAcrossPanels(session) {
  const { page, state } = session;
  await toggle(page, 750, true);
  await toggle(page, 748, true);
  const writes = state.requests.filter(request => request.method === 'POST').length;
  await page.evaluate(() => window.reopen());
  await waitPressed(page, 750, true);
  await waitPressed(page, 748, true);
  await page.locator(action('learning-return')).click();
  await page.locator(`${card(748)}.is-learning-target`).waitFor();
  await page.waitForFunction(() => window.learningScrolls.at(-1) === 748);
  assert.equal(state.requests.filter(request => request.method === 'POST').length, writes, 'reopen/jump reads persisted state without writing it');
  assert.equal(await page.evaluate(() => window.learningScrolls.at(-1)), 748);
}

async function farWindow(session) {
  const { page, state } = session;
  setLearned(state, 120);
  await page.evaluate(() => window.panel.refresh());
  if (session.width === 390) await page.screenshot({ path: path.join(session.compiled, 'learning-top-390.png') });
  const before = state.requests.length;
  await page.locator(action('learning-return')).click();
  await page.locator(`${card(120)}.is-learning-target`).waitFor();
  await page.waitForFunction(() => window.learningScrolls.at(-1) === 120);
  const ids = await renderedIds(page);
  assert.equal(ids.length, 24, 'jump loads one bounded metadata page, not the entire history');
  assert.ok(ids.includes(119) && ids.includes(121), 'both neighboring works remain available');
  assert.equal(await page.evaluate(() => window.learningScrolls.at(-1)), 120);
  if (session.width === 390) {
    // Override smooth motion only for the capture; the ordinary jump was already asserted.
    await page.locator(card(120)).evaluate(element => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
    await page.screenshot({ path: path.join(session.compiled, 'learning-target-390.png') });
  }
  const jump = state.requests.slice(before).filter(request => request.p === '/api/model-mr/works');
  assert.equal(jump.length, 1, 'jump uses one around-id metadata request, never hundreds of pages');
  assert.equal(jump[0].query.around_id, '120');
  assert.ok(state.ids.indexOf(120) > 500);
  assert.equal(state.requests.slice(before).filter(request => /^\/api\/model-mr\/works\/\d+$/.test(request.p) || request.p.startsWith('/media/')).length, 0);
  await page.locator(action('load-more')).click();
  await page.waitForFunction(() => document.querySelectorAll('.model-work-list-full > .model-work-card').length > 24);
  const extended = await renderedIds(page);
  assert.equal(new Set(extended).size, extended.length, 'append pagination has no duplicate cards');
  assert.ok(extended.includes(108), 'older neighbors can be loaded');
  state.ids.unshift(751);
  await page.evaluate(() => window.panel.refresh());
  const refreshed = await renderedIds(page);
  assert.ok(refreshed.includes(120), 'background refresh keeps the learning window');
  assert.ok(!refreshed.includes(751), 'a new release must not switch away from the learning window');
  assert.deepEqual(refreshed.slice(0, extended.length), extended, 'new insertion preserves the displayed learning range');
  await page.locator(action('load-previous')).click();
  await page.waitForFunction(first => Number(document.querySelector('.model-work-list-full > .model-work-card')?.dataset.workId) > first, refreshed[0]);
  const previous = await renderedIds(page);
  assert.ok(previous.includes(120), 'loading preceding neighbors retains the current study target');
  await page.locator(action('works-latest')).click();
  await page.locator(card(751)).waitFor();
  assert.equal((await renderedIds(page))[0], 751, 'returning to latest videos is an explicit action');
}

async function failedSave(session) {
  const { page, state } = session;
  const id = 733; // Deep enough that the global toolbar is outside the viewport.
  state.failedWrites = 1;
  await page.locator(mark(id)).click();
  await page.waitForFunction(() => document.querySelector('.model-learning-status')?.textContent.includes('合成保存失败'));
  assert.equal(await page.locator(mark(id)).getAttribute('aria-pressed'), 'false');
  assert.equal(state.learning.last_work_id, null);
  assert.equal(await page.locator(action('learning-return')).isDisabled(), true, 'a failed save must not claim a persisted learning position');
  const feedback = page.locator(`${card(id)} .model-learning-card-feedback`);
  assert.match(await feedback.innerText(), /合成保存失败/);
  assert.equal(await feedback.getAttribute('role'), 'status', 'deep-card failures have local accessible feedback');
  assert.equal(await page.locator('.model-learning-card-feedback').count(), 1, 'error belongs only to the card that failed');
  await feedback.scrollIntoViewIfNeeded();
  if (session.width === 390) await page.screenshot({ path: path.join(session.compiled, 'learning-error-390.png') });
  await feedback.locator(action('learning-retry-work')).click();
  await waitPressed(page, id, false);
  assert.equal(await feedback.count(), 0, 'confirmed reread clears local failure feedback');
  await toggle(page, id, true);
  assert.equal(state.learning.last_work_id, id);
}

async function conflictRefresh(session) {
  const { page, state } = session;
  setLearned(state, 750); // Another tab saved while this tab still has revision 0.
  await page.locator(mark(749)).click();
  await page.waitForFunction(() => document.querySelector('.model-learning-status')?.textContent.includes('另一页面更新'));
  assert.equal(await page.locator(mark(750)).getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator(mark(749)).getAttribute('aria-pressed'), 'false');
  assert.equal(state.requests.filter(request => request.method === 'POST').length, 1, 'conflict must not automatically replay the owner action');
  await page.locator(`.model-learning-toolbar ${action('learning-retry')}`).click();
  await waitPressed(page, 750, true);
  await waitPressed(page, 749, false);
  assert.equal(state.learning.last_work_id, 750);
  await toggle(page, 749, true);
  assert.equal(state.learning.last_work_id, 749);
  const posts = state.requests.filter(request => request.method === 'POST');
  assert.deepEqual(posts.map(request => request.payload.expected_revision), [0, 1]);
}

async function pendingAndStaleRead(session) {
  const { page, state } = session;
  const readGate = deferred();
  const writeGate = deferred();
  state.holdWrite = writeGate;
  await page.locator(mark(750)).click();
  await page.waitForFunction(() => document.querySelector('[data-model-action="toggle-learned"][data-work-id="750"]')?.disabled);
  assert.equal(await page.locator(mark(750)).getAttribute('aria-pressed'), 'false', 'pending save must not be presented as confirmed');
  assert.equal(await page.locator(mark(749)).isDisabled(), true, 'serialize owner changes while a revision save is pending');
  state.holdRead = readGate;
  const beforeReads = state.requests.filter(request => request.p === '/api/model-mr/learning').length;
  await page.evaluate(() => { window.backgroundRead = window.panel.refresh(); });
  await waitRequests(state, request => request.p === '/api/model-mr/learning', beforeReads);
  writeGate.resolve();
  await page.waitForFunction(() => document.querySelector('[data-model-action="toggle-learned"][data-work-id="750"]')?.getAttribute('aria-pressed') === 'true');
  readGate.resolve();
  await page.evaluate(() => window.backgroundRead);
  await waitPressed(page, 750, true);
  assert.equal(state.learning.last_work_id, 750, 'late pre-save read cannot roll back new learning state');
}

async function readFailure(session) {
  const { page, state } = session;
  setLearned(state, 749);
  state.failedReads = 1;
  await page.evaluate(() => window.reopen());
  assert.equal((await renderedIds(page)).length, 24, 'learning read failure must not block ordinary video browsing');
  await page.locator(`.model-learning-toolbar ${action('learning-retry')}`).click();
  await waitPressed(page, 749, true);
  assert.equal(await page.locator(action('learning-return')).isEnabled(), true);
}

async function preserveVideo(session) {
  const { page, state } = session;
  await page.locator(`${action('open-detail')}[data-work-id="750"][data-detail-tab="video"]`).click();
  await page.locator('video').waitFor();
  await page.waitForFunction(() => document.querySelector('video').readyState >= 1);
  await page.evaluate(async () => {
    const video = document.querySelector('video');
    window.originalVideo = video;
    window.removals = 0; window.srcChanges = 0; window.loads = 0;
    const load = video.load.bind(video);
    video.load = () => { window.loads++; return load(); };
    new MutationObserver(records => {
      for (const record of records) {
        if (record.type === 'attributes' && record.target === video) window.srcChanges++;
        for (const node of record.removedNodes) if (node === video || node.contains?.(video)) window.removals++;
      }
    }).observe(document.querySelector('#root'), { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
    video.currentTime = 20; video.muted = true; await video.play();
  });
  await toggle(page, 750, true);
  await toggle(page, 749, true);
  await toggle(page, 749, false);
  setLearned(state, 748);
  await page.evaluate(() => window.panel.refresh());
  await waitPressed(page, 748, true);
  assert.deepEqual(await page.evaluate(() => ({ same: document.querySelector('video') === window.originalVideo,
    connected: window.originalVideo.isConnected, paused: window.originalVideo.paused,
    removals: window.removals, srcChanges: window.srcChanges, loads: window.loads,
    progressed: window.originalVideo.currentTime >= 20 })),
  { same: true, connected: true, paused: false, removals: 0, srcChanges: 0, loads: 0, progressed: true });
}

async function pendingPageNavigation(session) {
  const { page, state } = session;
  setLearned(state, 120);
  await page.evaluate(() => window.panel.refresh());
  for (const pagingAction of ['load-more', 'load-previous']) {
    const gate = deferred();
    state.holdWorks = gate;
    const before = state.requests.filter(request => request.p === '/api/model-mr/works').length;
    await page.locator(action(pagingAction)).click();
    await waitRequests(state, request => request.p === '/api/model-mr/works', before);
    assert.equal(await page.locator(action('learning-return')).isDisabled(), true, 'pending page navigation cannot be superseded by a stuck locator');
    gate.resolve();
    await page.waitForFunction(() => !document.querySelector('[data-model-action="learning-return"]')?.disabled);
    await page.locator(action('learning-return')).click();
    await page.locator(`${card(120)}.is-learning-target`).waitFor();
    await page.waitForFunction(() => !document.querySelector('[data-model-action="load-more"]')?.disabled);
    assert.ok((await renderedIds(page)).includes(120));
  }
}

async function lateLocatorRespectsVideo(session) {
  const { page, state } = session;
  setLearned(state, 120);
  await page.evaluate(() => window.panel.refresh());
  const gate = deferred();
  state.holdWorks = gate;
  const before = state.requests.filter(request => request.p === '/api/model-mr/works').length;
  await page.locator(action('learning-return')).click();
  await waitRequests(state, request => request.p === '/api/model-mr/works', before);
  await page.locator(`${action('open-detail')}[data-work-id="750"][data-detail-tab="video"]`).click();
  await page.waitForFunction(() => document.querySelector('video')?.readyState >= 1);
  await page.evaluate(async () => {
    window.originalVideo = document.querySelector('video');
    window.originalVideo.currentTime = 20; window.originalVideo.muted = true;
    await window.originalVideo.play();
  });
  gate.resolve();
  await page.waitForFunction(() => !document.querySelector('[data-model-action="learning-return"]')?.disabled);
  assert.equal(await page.evaluate(() => document.querySelector('video') === window.originalVideo && window.originalVideo.isConnected && !window.originalVideo.paused), true,
    'a late locator response must not remove the video opened by a later explicit owner action');
  assert.ok((await renderedIds(page)).includes(750));
  assert.equal(await page.locator(card(120)).count(), 0, 'discard the stale locator window after owner navigation');
}

async function main() {
  const compiled = path.resolve(process.argv[2] || '');
  const media = fs.readFileSync(process.argv[3]);
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE || undefined, args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] });
  let passed = 0, failed = 0;
  try {
    for (const width of [390, 1280]) {
      for (const scenario of [emptyAndMark, persistAcrossPanels, farWindow, failedSave, conflictRefresh, pendingAndStaleRead, readFailure, preserveVideo, pendingPageNavigation, lateLocatorRespectsVideo]) {
        const session = await mount(browser, compiled, media, width);
        const label = `${scenario.name} ${width}`;
        try {
          await scenario(session);
          assert.deepEqual(session.errors, []);
          assert.deepEqual(session.state.unexpected, []);
          assert.equal(await session.page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'learning controls must not overflow the phone viewport');
          console.log(JSON.stringify({ case: label, result: 'PASS', syntheticOnly: true }));
          passed++;
        } catch (error) {
          failed++; console.error(`${label}: ${error.stack}`);
        } finally { await session.page.close(); }
      }
    }
  } finally { await browser.close(); }
  console.log(JSON.stringify({ passed, failed, total: passed + failed, productionRequests: 0, realPaidCalls: 0, iPhoneAcceptance: false }));
  if (failed) process.exitCode = 1;
}

main().catch(error => { console.error(error); process.exitCode = 1; });
