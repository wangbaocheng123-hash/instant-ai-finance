/* Synthetic APIs/media only; no credentials, production requests, or paid calls.
 * Usage: node video-refresh-browser.cjs <compiled-dir> <synthetic-video>
 * Compile ModelMrPanel.ts + BloggerPanel.ts to ES2020 modules before running.
 * A 90-second local ffmpeg color/sine clip is sufficient; use WebM when the
 * installed Chromium does not include H.264/AAC codecs. This is Chromium DOM
 * lifecycle coverage, not iPhone/native-fullscreen or mobile-network acceptance.
 * VIDEO_REFRESH_LONG=1 adds simultaneous 65-second, normal-speed playback in
 * both modules, with background refresh at 20/40/60 seconds. The video starts
 * at offset 20, so provide at least 90 seconds. VIDEO_REFRESH_ONLY_RACES=1 runs
 * only the delayed async/navigation cases. All artifacts stay outside Git.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const origin = 'http://127.0.0.1:19863';
const creatorId = '11111111-2222-4333-8444-555555555555';
const workKey = 'a'.repeat(64);

function fixtures() {
  return { available: true, revision: 1, processingState: 'queued', processingUpdated: 1,
    reads: { works: 0, detail: 0, processing: 0 }, workPages: 1 };
}

function processing(state, blogger) {
  const done = state.processingState === 'done';
  return {
    enabled: true, failures: 0, enabled_since: 1, last_reconciled: 2,
    worker_running: true, worker_last_seen: 3, initial_recovery_hours: 48,
    daily_call_limit: 20, max_video_minutes: 20, speech_configured: true, keywords_configured: true,
    summary: { queued: done ? 0 : 1, running: 0, done: done ? 1 : 0, configuration: 0, review: 0, quota: 0, conflict: 0, total: 1 },
    items: [{ id: 1, work_id: 1, work_key: workKey, title: '合成视频', creator_name: '合成博主', kind: 'pipeline', automatic: true,
      state: state.processingState, phase: done ? 'complete' : 'asr', message: done ? '合成处理完成' : '合成等待处理', updated: state.processingUpdated,
      steps: { asr: { state: done ? 'done' : 'queued', message: '原文状态' }, keywords: { state: done ? 'done' : 'waiting', message: '关键词状态' } },
      ...(blogger ? { mode: '自动' } : {}),
    }],
  };
}

function work(state, blogger, id = 1) {
  const base = {
    id, title: `合成视频 ${id} · 修订 ${state.revision}`, description: '', work_type: 'video', title_source: 'source', title_confidence: null,
    published_at: '2026-10-11T08:00:00+08:00', captured_at: '2026-10-11T08:01:00+08:00',
    media_available: true, video_available: true, video_url: `/media/${blogger ? 'blogger' : 'model-mr'}/synthetic.mp4`,
    image_urls: [], image_count: 0, has_video_text: true, has_interpretation: false, keywords: [], keyword_revision: 'r1',
    keyword_info: { categories: {}, keywords: [] }, comment_count: 0,
  };
  return blogger ? { ...base, work_key: workKey, creator_id: creatorId, platform: 'douyin', source_work_id: 'synthetic', source_url: '',
    processing_status: 'ready', transfer: { status: 'verified', source_revision: state.revision, received_at: base.captured_at,
      media_expected: 1, media_received: 1, comments_expected: 0, comments_received: 0 } } : base;
}

function detail(state, blogger) {
  const contents = { video_text: { text: `合成原文修订 ${state.revision}`, official: true, source: 'synthetic' }, interpretation: { text: '' },
    transcripts: [], comments: [], comment_total: 0, stock_mentions: { items: [], stock_count: 0, total_comments: 0, uncertain: [] },
    capabilities: { video: true, save_title: true, save_video_text: true, transcribe_video: false, doubao_asr: false } };
  return blogger ? { ...work(state, true), ...contents } : { work: work(state, false), ...contents };
}

async function mount(browser, compiled, media, kind, width) {
  const blogger = kind === 'blogger';
  const state = fixtures();
  const page = await browser.newPage({ viewport: { width, height: 844 }, isMobile: width === 390, hasTouch: width === 390 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  // Drive automatic hooks explicitly, without wall-clock waits or a near-bottom
  // IntersectionObserver racing the isolated refresh under test.
  await page.addInitScript(() => {
    window.setInterval = () => 0;
    window.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
  });
  const creator = { creator_id: creatorId, display_name: '合成博主', platform: 'douyin', work_count: 1,
    latest_published_at: '2026-10-11', latest_captured_at: '2026-10-11',
    status_counts: { works: 1, transferring: 0, awaiting_asr_approval: 0, ready: 1, failed: 0 } };
  await page.route(`${origin}/**`, async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    if (p === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><div id="root"></div><script type="module">import {${blogger ? 'BloggerPanel' : 'ModelMrPanel'}} from '/${blogger ? 'BloggerPanel' : 'ModelMrPanel'}.js';window.panel=new ${blogger ? 'BloggerPanel' : 'ModelMrPanel'}();document.querySelector('#root').append(window.panel.element);window.panel.element.hidden=false;await window.panel.refresh();window.ready=true;</script>` });
    if (['/BloggerPanel.js', '/ModelMrPanel.js', '/ModelMrComments.js', '/api.js', '/ModelMrComments', '/api'].includes(p))
      return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.join(compiled, `${p.slice(1)}${p.endsWith('.js') ? '' : '.js'}`), 'utf8') });
    if (p.startsWith('/media/')) {
      const range = route.request().headers().range?.match(/^bytes=(\d+)-(\d*)$/);
      const start = range ? Number(range[1]) : 0;
      const end = range && range[2] ? Math.min(Number(range[2]), media.length - 1) : media.length - 1;
      return route.fulfill({ status: range ? 206 : 200, contentType: media.subarray(0, 4).equals(Buffer.from([0x1a, 0x45, 0xdf, 0xa3])) ? 'video/webm' : 'video/mp4',
        headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${media.length}` } : {}) }, body: media.subarray(start, end + 1) });
    }
    if (p.endsWith('/status')) return json({ available: state.available, message: '合成连接暂时不可用',
      counts: { creators: 1, works: state.workPages, media: 1, transferring: 0, awaiting_asr_approval: 0, ready: 1, failed: 0 } });
    if (p.endsWith('/processing')) { state.reads.processing++; return json(processing(state, blogger)); }
    if (p === '/api/model-mr/learning') return json({ revision: 0, learned_work_ids: [], last_work_id: null });
    if (p.endsWith('/chat/config')) return json({ enabled: false, models: [] });
    if (p.endsWith('/thoughts')) return json({ categories: [{ id: 1, name: '合成主题', description: '', level: 1, parent_id: null, video_count: 1 }] });
    if (p.endsWith('/creators')) return json({ items: [creator], count: 1 });
    if (p.endsWith('/works')) {
      state.reads.works++;
      if (blogger) return json({ creator, items: [work(state, true)], count: 1 });
      const offset = Number(url.searchParams.get('offset') || 0);
      return json({ items: [work(state, false, offset + 1)], count: 1, offset, total: state.workPages,
        has_more: offset + 1 < state.workPages, keywords: [], message: '' });
    }
    if (p === `/api/${blogger ? 'blogger-library' : 'model-mr'}/works/${blogger ? workKey : 1}`) {
      state.reads.detail++;
      return json(detail(state, blogger));
    }
    throw new Error(`Unexpected synthetic request: ${p}`);
  });
  await page.goto(`${origin}/`);
  await page.waitForFunction(() => window.ready);
  if (blogger) {
    await page.locator('[data-blogger-action="open-creator"]').first().click();
    await page.locator('[data-blogger-action="open-work"]').click();
  } else {
    if (kind === 'thoughts') {
      await page.locator('[data-model-tab="thoughts"]').click();
      await page.locator('[data-model-action="thought-open"]').click();
    }
    await page.locator('[data-model-action="open-detail"][data-detail-tab="video"]').first().click();
  }
  await watchVideo(page);
  return { page, state, errors };
}

async function watchVideo(page) {
  await page.locator('video').waitFor();
  await page.waitForFunction(() => document.querySelector('video').readyState >= 1);
  await page.evaluate(() => {
    window.videoObserver?.disconnect();
    const video = document.querySelector('video');
    window.originalVideo = video;
    window.videoRemovals = 0;
    window.videoAttributes = 0;
    window.videoLoadCalls = 0;
    const load = video.load.bind(video);
    video.load = () => { window.videoLoadCalls++; return load(); };
    const observe = records => {
      for (const record of records) {
        if (record.type === 'attributes' && record.target === video && record.attributeName === 'src') window.videoAttributes++;
        for (const node of record.removedNodes) if (node === video || node.contains?.(video)) window.videoRemovals++;
      }
    };
    window.videoObserver = new MutationObserver(observe);
    window.videoObserver.observe(document.querySelector('#root'), { subtree: true, childList: true, attributes: true, attributeFilter: ['src'] });
    video.currentTime = 20;
  });
}

async function assertUninterrupted(page, label) {
  const actual = await page.evaluate(() => ({ same: document.querySelector('video') === window.originalVideo,
    connected: window.originalVideo.isConnected, removals: window.videoRemovals, srcChanges: window.videoAttributes,
    loadCalls: window.videoLoadCalls, currentTime: window.originalVideo.currentTime }));
  assert.deepEqual({ ...actual, currentTime: undefined }, { same: true, connected: true, removals: 0, srcChanges: 0, loadCalls: 0, currentTime: undefined }, label);
  assert.ok(actual.currentTime >= 19.9, `${label}: playback position must not reset`);
}

async function runCase(browser, compiled, media, kind, width, scenario) {
  const { page, state, errors } = await mount(browser, compiled, media, kind, width);
  const label = `${kind} ${width} ${scenario}`;
  try {
    if (scenario === 'refresh-playing') {
      await page.evaluate(async () => { window.originalVideo.muted = true; await window.originalVideo.play(); });
    } else if (scenario === 'refresh-buffering') {
      await page.evaluate(() => { window.originalVideo.dispatchEvent(new Event('waiting')); });
    }
    state.revision = 2;
    if (scenario === 'error') {
      await page.evaluate(() => window.panel.setError('合成短时网络错误'));
    } else if (scenario === 'unavailable') {
      state.available = false;
      await page.evaluate(() => window.panel.refresh());
    } else if (scenario === 'pagination') {
      state.workPages = 2;
      await page.evaluate(async () => { window.panel.hasMoreWorks = true; await window.panel.loadMoreWorks(); });
    } else if (scenario === 'processing-completion') {
      state.processingState = 'done'; state.processingUpdated = 2;
      await page.evaluate(() => window.panel.pollProcessing());
      assert.match(await page.locator('.blogger-processing').innerText(), /合成处理完成/);
    } else {
      const before = state.reads.works;
      for (let i = 0; i < 3; i++) await page.evaluate(() => window.panel.refresh());
      assert.ok(state.reads.works > before, 'background data must still refresh while video is mounted');
    }
    await assertUninterrupted(page, label);
    if (scenario === 'refresh-playing') assert.equal(await page.evaluate(() => window.originalVideo.paused), false, 'active playback must continue');
    // Recover transport, then deliberately leave video. Only explicit navigation
    // may remove this node; deferred newer data must be visible afterward.
    state.available = true;
    await page.evaluate(() => window.panel.refresh());
    await assertUninterrupted(page, `${label} recovery`);
    if (kind === 'blogger') {
      await page.locator('[data-blogger-action="detail-tab"][data-detail-tab="text"]').click();
      assert.equal(await page.locator('video').count(), 0);
      assert.equal(await page.locator('#blogger-video-text').inputValue(), '合成原文修订 2');
      await page.locator('[data-blogger-action="back-works"]').click();
      assert.match(await page.locator('.blogger-work-card h3').innerText(), /修订 2/);
    } else {
      await page.locator('[data-model-action="close-detail"]').first().click();
      assert.equal(await page.locator('video').count(), 0);
      if (kind === 'works') assert.match(await page.locator('.model-work-card h3').first().innerText(), /修订 2/);
      // Work detail is fetched afresh when the owner deliberately reopens it.
      await page.locator('[data-model-action="open-detail"][data-detail-tab="text"]').first().click();
      assert.equal(await page.locator('#model-video-text-1').inputValue(), '合成原文修订 2');
      await page.locator('[data-model-tab="chat"]').click();
      assert.equal(await page.locator('video').count(), 0);
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ case: label, result: 'PASS', syntheticOnly: true, iPhoneE2E: false }));
  } finally { await page.close(); }
}

async function sustainedPlayback(browser, compiled, media) {
  const sessions = [];
  try {
    for (const kind of ['works', 'blogger']) {
      const session = await mount(browser, compiled, media, kind, 390);
      session.kind = kind;
      sessions.push(session);
      await session.page.evaluate(async () => { window.originalVideo.muted = true; await window.originalVideo.play(); });
    }
    const started = Date.now();
    for (let step = 1; step <= 3; step++) {
      await new Promise(resolve => setTimeout(resolve, 20_000));
      for (const { page, state, kind } of sessions) {
        state.revision++;
        await page.evaluate(() => window.panel.refresh());
        await assertUninterrupted(page, `${kind} real-time refresh ${step}`);
        assert.equal(await page.evaluate(() => window.originalVideo.paused), false);
      }
      console.log(JSON.stringify({ sustainedPlayback: 'running', elapsedSeconds: Math.round((Date.now() - started) / 1000), refreshesPerPanel: step }));
    }
    await new Promise(resolve => setTimeout(resolve, 5_000));
    for (const { page, kind, errors } of sessions) {
      await assertUninterrupted(page, `${kind} 65-second playback`);
      const position = await page.evaluate(() => window.originalVideo.currentTime);
      assert.ok(position >= 80, `${kind}: real video must advance at least 60 seconds from offset 20`);
      assert.equal(await page.evaluate(() => window.originalVideo.paused), false);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ sustainedPlayback: kind, result: 'PASS', elapsedSeconds: (Date.now() - started) / 1000,
        positionSeconds: position, playbackRate: 1, automaticRefreshes: 3, iPhoneE2E: false }));
    }
  } finally { await Promise.all(sessions.map(({ page }) => page.close())); }
}

async function completionRace(browser, compiled, media, operation) {
  const { page, errors } = await mount(browser, compiled, media, 'works', 390);
  let release;
  let started;
  const startedPromise = new Promise(resolve => { started = resolve; });
  const releasePromise = new Promise(resolve => { release = resolve; });
  try {
    const endpoint = operation === 'chat' ? '/api/model-mr/chat' : '/api/model-mr/works/1/transcribe';
    await page.route(`${origin}${endpoint}`, async route => {
      assert.equal(route.request().method(), 'POST');
      started();
      await releasePromise;
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(operation === 'chat'
        ? { answer: '合成延迟回答' } : { text: '合成延迟原文', message: '合成已完成', cached: true }) });
    });
    if (operation === 'chat') {
      await page.evaluate(() => { window.panel.chatConfig = { enabled: true, models: [{ id: 'synthetic', label: '合成' }], default_model: 'synthetic' }; });
      await page.locator('[data-model-tab="chat"]').click();
      await page.locator('#modelMrChatInput').fill('合成问题');
      await page.evaluate(() => { window.pendingOperation = window.panel.sendChat(); });
    } else {
      await page.locator('[data-model-action="detail-tab"][data-detail-tab="text"]').click();
      await page.evaluate(() => { window.pendingOperation = window.panel.transcribe(1, 'video'); });
    }
    await startedPromise;
    if (operation === 'chat') await page.locator('[data-model-tab="works"]').click();
    else await page.locator('[data-model-action="detail-tab"][data-detail-tab="video"]').click();
    await watchVideo(page);
    await page.evaluate(async () => { window.originalVideo.muted = true; await window.originalVideo.play(); });
    release();
    await page.evaluate(() => window.pendingOperation);
    await assertUninterrupted(page, `${operation} late completion after switching to video`);
    assert.equal(await page.evaluate(() => window.originalVideo.paused), false);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ case: `${operation}-completion-race`, result: 'PASS', syntheticOnly: true, realPaidCalls: 0 }));
  } finally { release(); await page.close(); }
}

async function offlineNavigation(browser, compiled, media) {
  const { page, state, errors } = await mount(browser, compiled, media, 'works', 390);
  try {
    await page.evaluate(() => window.panel.setError('合成断网'));
    await assertUninterrupted(page, 'offline error preserves mounted player');
    await page.locator('[data-model-tab="chat"]').click();
    assert.equal(await page.locator('video').count(), 0, 'explicit chat navigation must work after an error');
    await page.locator('#modelMrChatForm').waitFor();
    await page.locator('[data-model-tab="works"]').click();
    await watchVideo(page);
    state.available = false;
    await page.evaluate(() => window.panel.refresh());
    await assertUninterrupted(page, 'unavailable status preserves mounted player');
    await page.locator('[data-model-tab="chat"]').click();
    assert.equal(await page.locator('video').count(), 0, 'explicit chat navigation must work after unavailable status');
    await page.locator('#modelMrChatForm').waitFor();
    await page.locator('[data-model-tab="works"]').click();
    await page.locator('video').waitFor();
    await page.locator('[data-model-action="close-detail"]').click();
    assert.equal(await page.locator('video').count(), 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ case: 'offline-cached-navigation', result: 'PASS', syntheticOnly: true }));
  } finally { await page.close(); }
}

async function lateWorkDetail(browser, compiled, media) {
  const { page, state, errors } = await mount(browser, compiled, media, 'works', 390);
  let release;
  let started;
  const startedPromise = new Promise(resolve => { started = resolve; });
  const releasePromise = new Promise(resolve => { release = resolve; });
  try {
    const pendingDetail = { ...detail(state, false), work: work(state, false, 2) };
    await page.route(`${origin}/api/model-mr/works/2`, async route => {
      started();
      await releasePromise;
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(pendingDetail) });
    });
    await page.evaluate(second => {
      window.panel.works.push(second);
      window.pendingOperation = window.panel.openWorkDetail(2, 'text');
    }, work(state, false, 2));
    await startedPromise;
    // Explicitly return to already-cached work 1 while work 2 is still loading.
    await page.evaluate(() => window.panel.openWorkDetail(1, 'video'));
    await watchVideo(page);
    await page.evaluate(async () => { window.originalVideo.muted = true; await window.originalVideo.play(); });
    release();
    await page.evaluate(() => window.pendingOperation);
    await assertUninterrupted(page, 'late work 2 detail cannot replace work 1 player');
    assert.equal(await page.evaluate(() => window.originalVideo.paused), false);
    await page.locator('[data-model-action="close-detail"][data-work-id="1"]').click();
    assert.equal(await page.locator('video').count(), 0);
    assert.equal(await page.locator('#model-video-text-2').inputValue(), '合成原文修订 1');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ case: 'late-open-work-detail', result: 'PASS', syntheticOnly: true }));
  } finally { release(); await page.close(); }
}

async function main() {
  const [compiled, clip] = process.argv.slice(2);
  assert.ok(compiled && clip, 'Usage: node video-refresh-browser.cjs <compiled-dir> <synthetic-video>');
  const media = fs.readFileSync(clip);
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE || undefined });
  let passed = 0, failed = 0;
  try {
    for (const width of process.env.VIDEO_REFRESH_ONLY_RACES === '1' ? [] : [390, 1280]) {
      for (const kind of ['works', 'thoughts', 'blogger']) {
        const scenarios = ['refresh-paused', 'refresh-playing', 'refresh-buffering', 'error', 'unavailable',
          ...(kind === 'works' ? ['pagination'] : []), ...(kind === 'blogger' ? ['processing-completion'] : [])];
        for (const scenario of scenarios) {
          try { await runCase(browser, compiled, media, kind, width, scenario); passed++; }
          catch (error) { failed++; console.error(`${kind} ${width} ${scenario}: ${error.stack}`); }
        }
      }
    }
    for (const operation of ['chat', 'transcribe']) {
      try { await completionRace(browser, compiled, media, operation); passed++; }
      catch (error) { failed++; console.error(`${operation} completion race: ${error.stack}`); }
    }
    for (const scenario of [offlineNavigation, lateWorkDetail]) {
      try { await scenario(browser, compiled, media); passed++; }
      catch (error) { failed++; console.error(`${scenario.name}: ${error.stack}`); }
    }
    if (!failed && process.env.VIDEO_REFRESH_LONG === '1') await sustainedPlayback(browser, compiled, media);
  } finally { await browser.close(); }
  console.log(JSON.stringify({ passed, failed, total: passed + failed, productionRequests: 0, realPaidCalls: 0 }));
  if (failed) process.exitCode = 1;
}

main().catch(error => { console.error(error); process.exitCode = 1; });
