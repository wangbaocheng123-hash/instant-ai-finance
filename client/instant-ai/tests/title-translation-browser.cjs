/* Foreground billing acceptance. All HTTP APIs/models are synthetic; no real keys or paid calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate) {
  for (let i = 0; i < 100; i++) { if (predicate()) return; await sleep(50); }
  throw Error('Timed out waiting for synthetic translation');
}
function item(id) {
  const title = id === 3 ? '黄金价格上涨' : id === 4 ? '코스피 상승, 반도체주 강세'
    : id === 8 ? '韓國 금값 상승, 원화 약세' : id === 2001 ? '韓國 증시, AI 반도체株 강세'
      : `English market headline number ${id}`;
  return { id, title, summary: '',
    url: 'https://example.test/story', thumbnail_url: '/thumb.png', published_at: '2026-11-23T08:00:00Z',
    first_seen_at: '2026-11-23T08:00:00Z', event_type: '市场', trust_level: 4, importance_score: 70,
    is_read: false, is_saved: false, topics: ['黄金'], sources: ['合成新闻源'], source_count: 1 };
}

async function mount(browser, dist, width, state) {
  const page = await browser.newPage({ viewport: { width, height: 844 }, ...(state.userAgent ? { userAgent: state.userAgent } : {}) });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    window.hiddenForTest = false;
    Object.defineProperty(document, 'visibilityState', { get: () => window.hiddenForTest ? 'hidden' : 'visible' });
  });
  const translated = row => ({ ...row, translated_title: state.cache.get(row.title) || '' });
  const status = () => ({ enabled: true, configured: true, provider: 'doubao-seed-translation-250915',
    provider_label: '豆包标题翻译', cached_titles: state.cache.size, remaining_characters_today: 50_000 });
  const handleRoute = async route => {
    const u = new URL(route.request().url()); assert.equal(u.origin, 'http://headline.test');
    const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    if (u.pathname === '/api/translate' || u.pathname === '/api/watch-events/translate') {
      const body = route.request().postDataJSON();
      assert.equal(body.visible_only, true);
      const watch = u.pathname === '/api/watch-events/translate';
      if (!watch) { assert.equal(body.max_new, 1); assert.equal(body.item_ids.length, 1); }
      const id = watch ? `watch:${body.kind}:${body.key}` : body.item_ids[0];
      const row = watch ? { title: body.expected_title } : state.items.find(x => x.id === id) || item(id);
      assert.equal(await page.evaluate(() => document.visibilityState), 'visible');
      state.requests.push(id); state.running++; state.maxRunning = Math.max(state.maxRunning, state.running);
      if (state.holdNext) {
        state.holdNext = false;
        await new Promise(resolve => { state.release = resolve; });
      } else await sleep(120);
      const title = `中文标题 ${id}`;
      if (!state.fail) state.cache.set(row.title, title);
      state.running--;
      return json({ ok: !state.fail, translations: state.fail ? {} : { [id]: title },
        original_titles: { [id]: row.title }, providers: { [id]: status().provider },
        translated_count: state.fail ? 0 : 1, cached_count: 0, skipped_count: 0, pending_count: 0,
        errors: state.fail ? ['unconfirmed'] : [], quota_exhausted: false, status: status() });
    }
    if (u.pathname.startsWith('/api/')) {
      if (u.pathname === '/api/auth/status') return json({ required: true, authenticated: true, session_days: 30 });
      if (u.pathname === '/api/status') {
        if (state.holdStatus) {
          state.holdStatus = false;
          await new Promise(resolve => { state.releaseStatus = resolve; });
        }
        return json({ collection: { running: false, interval_seconds: 300 } });
      }
      if (u.pathname === '/api/items') return json((u.searchParams.get('topic') === '华尔街'
        ? state.wallItems : state.items).map(translated));
      if (u.pathname === '/api/hot') return json([]);
      if (u.pathname === '/api/translation/status') return json(status());
      if (u.pathname === '/api/watch-events') {
        if (state.holdWatch) {
          state.holdWatch = false;
          await new Promise(resolve => { state.releaseWatch = resolve; });
        }
        if (state.watch) {
          const payload = JSON.parse(JSON.stringify(state.watch));
          for (const group of [payload.events, payload.discovery?.events, payload.monetaryPolicy?.signals]) {
            for (const row of group || []) row.translated_title = state.staleWatch ? '' : state.cache.get(row.title) || '';
          }
          return json(payload);
        }
        return json({ events: [], counts: {}, sync: null });
      }
      if (u.pathname === '/api/model-mr/status' || u.pathname === '/api/blogger-library/status') return json({ available: false, counts: {} });
      if (u.pathname === '/api/blogger-library/processing') return json({ enabled: false, summary: {}, items: [] });
      return json({});
    }
    if (u.pathname === '/thumb.png') return route.fulfill({ status: 204 });
    const name = u.pathname === '/' ? 'index.html' : u.pathname.slice(1);
    let data = fs.readFileSync(path.join(dist, name));
    if (name === 'index.html') data = Buffer.from(data.toString().replace('</head>',
      '<style>.ticker-board {display:none !important}</style></head>'));
    return route.fulfill({ contentType: name.endsWith('.html') ? 'text/html' : name.endsWith('.css') ? 'text/css' : 'text/javascript', body: data });
  };
  await page.route('**/*', route => handleRoute(route).catch(async error => {
    if (!page.isClosed()) errors.push(error.message);
    await route.abort().catch(() => {});
  }));
  await page.goto('http://headline.test/');
  await page.locator('.news-row h3').first().waitFor();
  return { page, errors };
}

async function main() {
  const [dist, output] = process.argv.slice(2); fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
  try {
    for (const width of [390, 1280]) {
      const state = { items: Array.from({ length: 20 }, (_, i) => item(i + 1)), cache: new Map(),
        wallItems: Array.from({ length: 12 }, (_, i) => item(i + 2001)),
        requests: [], running: 0, maxRunning: 0, holdNext: false, release: null, fail: false };
      const { page, errors } = await mount(browser, dist, width, state);
      await until(() => state.cache.size > 0);
      assert(!state.requests.includes(12), 'an offscreen title was translated');
      assert(!state.requests.includes(3), 'a Chinese title was submitted');
      await page.locator('[data-panel="global"] .news-row[data-item-id="8"]').scrollIntoViewIfNeeded();
      await until(() => state.cache.has(item(8).title));
      await page.evaluate(() => { window.retainedRow = document.querySelector('.news-row[data-item-id="8"]'); });
      await sleep(250);
      assert(await page.evaluate(() => window.retainedRow === document.querySelector('.news-row[data-item-id="8"]')));
      state.holdNext = true;
      await page.locator('[data-target="wall-street"]').first().click();
      await until(() => state.release !== null);
      await page.evaluate(() => { window.hiddenForTest = true; document.dispatchEvent(new Event('visibilitychange')); });
      const beforeHidden = state.requests.length;
      state.release(); state.release = null;
      await sleep(650);
      assert.equal(state.requests.length, beforeHidden, 'a new request started in background');
      await page.evaluate(() => { window.hiddenForTest = false; document.dispatchEvent(new Event('visibilitychange')); });
      await sleep(500);
      assert.equal(state.maxRunning, 1);
      await page.locator('[data-action="translate"]').click();
      const beforeOff = state.requests.length;
      await page.locator('[data-target="china"]').first().click();
      await sleep(600);
      assert.equal(state.requests.length, beforeOff, 'translation switch did not stop requests');
      await page.screenshot({ path: path.join(output, `visible-title-${width}.png`), fullPage: true });
      assert.deepEqual(errors, []);
      await page.close();
      state.items = [item(1)]; state.cache.set(item(1).title, '已缓存中文标题');
      const beforeReopen = state.requests.length;
      const reopened = await mount(browser, dist, width, state);
      await sleep(450);
      assert.equal(state.requests.length, beforeReopen, 'cached title caused another model call');
      assert.equal(await reopened.page.locator('.news-row h3').first().innerText(), '已缓存中文标题');
      await reopened.page.close();
      // A news snapshot can predate a completed translation while another API
      // delays Promise.all. Returning to the foreground must not erase it.
      for (const changeTitle of [false, true]) {
        const original = item(changeTitle ? 802 : 801);
        const updated = { ...original, title: 'Updated English market headline after a source correction' };
        const raced = { ...state, items: [original], cache: new Map(), requests: [],
          holdNext: true, release: null, holdStatus: false, releaseStatus: null };
        const racing = await mount(browser, dist, width, raced);
        await until(() => raced.release !== null);
        await sleep(5_100);
        if (changeTitle) raced.items = [updated];
        raced.holdStatus = true;
        await racing.page.evaluate(() => window.dispatchEvent(new Event('online')));
        await until(() => raced.releaseStatus !== null);
        raced.release(); raced.release = null;
        await until(() => raced.cache.has(original.title));
        await racing.page.waitForFunction(id => document.querySelector('.news-row h3')?.textContent === `中文标题 ${id}`, original.id);
        raced.releaseStatus(); raced.releaseStatus = null;
        if (changeTitle) await until(() => raced.cache.has(updated.title));
        await sleep(700);
        assert.equal(await racing.page.locator('.news-row h3').first().innerText(), `中文标题 ${original.id}`,
          'a stale loading response erased a completed translation until the owner toggled the switch');
        assert.equal(raced.requests.length, changeTitle ? 2 : 1,
          'reuse only the exact original title; source corrections require their own translation');
        assert.deepEqual(racing.errors, []);
        await racing.page.close();
      }
      const loading = { ...state, items: [item(851)], cache: new Map(), requests: [],
        holdWatch: true, releaseWatch: null };
      const firstPaint = await mount(browser, dist, width, loading);
      await until(() => loading.releaseWatch !== null && loading.cache.has(item(851).title));
      assert.equal(await firstPaint.page.locator('.news-row h3').first().innerText(), '中文标题 851',
        'news translation waited for an unrelated module to finish loading');
      loading.releaseWatch(); loading.releaseWatch = null;
      await sleep(500);
      assert.equal(loading.requests.length, 1);
      assert.equal(await firstPaint.page.locator('.news-row h3').first().innerText(), '中文标题 851');
      await firstPaint.page.close();
      const failed = { ...state, items: [item(901), item(902)], cache: new Map(), requests: [], fail: true };
      const failure = await mount(browser, dist, width, failed);
      await until(() => failed.requests.length === 1);
      await sleep(650);
      assert.equal(failed.requests.length, 1, 'an unconfirmed paid call was retried automatically');
      assert.equal(await failure.page.locator('.news-row h3').first().innerText(), item(901).title);
      assert.equal(await failure.page.locator('[data-action="translate"]').innerText(), '汉化待重试');
      assert(await failure.page.locator('#headlineNotice').isVisible(), 'interrupted translation was silent');
      assert(await failure.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        'the retry notice overflowed the mobile viewport');
      await failure.page.screenshot({ path: path.join(output, `interrupted-title-${width}.png`), fullPage: true });
      failed.fail = false;
      await failure.page.locator('[data-action="retry-headlines"]').click();
      await until(() => failed.cache.size === 2);
      await sleep(250);
      assert.equal(failed.requests.filter(id => id === 901).length, 2,
        'only an explicit owner retry may repeat the interrupted headline');
      assert.equal(await failure.page.locator('.news-row h3').first().innerText(), '中文标题 901');
      assert(!(await failure.page.locator('#headlineNotice').isVisible()));
      assert.deepEqual(failure.errors, []);
      await failure.page.close();
      console.log(JSON.stringify({ width, foregroundOnly: 'pass', offscreenAndChinese: 'pass',
        koreanVisibleAndCache: 'pass', cacheReopen: 'pass', oneInFlight: 'pass',
        hiddenStopsNext: 'pass', failureNoRetry: 'pass', staleLoadingSnapshot: 'pass', changedOriginalIsolated: 'pass',
        newsBeforeOtherModules: 'pass', visibleOwnerRetry: 'pass', realPaidCalls: 0 }));
    }
  } finally { await browser.close(); }
}
module.exports = { mount, item, sleep, until };
if (require.main === module) main().catch(e => { console.error(e); process.exitCode = 1; });
