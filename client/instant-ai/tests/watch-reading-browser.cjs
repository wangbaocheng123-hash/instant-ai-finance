/* Real DOM/layout and gestures, synthetic APIs only. No live model or phone claim. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { mount, item, sleep, until } = require('./title-translation-browser.cjs');
const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1';
const original = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm?year=2026';
const when = '2026-11-24T00:00:00Z';
function payload() {
  const event = { event_key: 'event-1', scope: 'home', source_kind: 'timeline', source_event_id: 'synthetic',
    title: 'Federal Reserve policy outlook and rate decision', event_date: '2026-12-01', event_time: '03:00',
    category: '宏观政策', importance: 5, event_status: 'planned', note: '关注官方渠道，不代表已经加息。', sources: [],
    monitoring: { coverage: 'verified', publisher: { name: '美联储', url: original }, release: { label: '北京时间待核验', windowStart: '', windowEnd: '' } },
    official_status: 'reachable', monitor_status: '官方渠道正常', candidate_status: '', pipeline_status: '',
    analysis_feedback: {}, latest_signal: null, match_count: 0, latest_matches: [], official_channels: [{ name: '美联储决议原始渠道', url: original, last_success_at: when, last_error: null }] };
  return { events: [event], counts: { total: 1, home: 1, zijin: 0, configured: 1, official_reachable: 1, signals_detected: 0, signals_delivered: 0 }, sync: null,
    monetaryPolicy: { coverage: '官方与已接入媒体，首发待核验。',
      monitoring: { defaultIntervalSeconds: 300, sources: [{ key: 'fed', name: '美联储政策源', state: 'stale', lastSuccessAt: when, nextCheckAt: when }] },
      signals: [{ key: 'policy-1', title: 'Fed official discusses interest rate outlook', bankName: '美联储', kindLabel: '官员表态线索', speaker: '测试官员', publishedAt: when, detectedAt: when, status: 'pending', pipelineStatus: '已锁定，等待输送', firstObservedSource: { publisher: '美联储', url: original, sourceRole: 'official-original' } }] },
    discovery: { enabled: true, coverage: '本月/次月官方日程。', sources: [], pendingDelivery: 0, failedDelivery: 0,
      events: [{ key: 'calendar-1', title: '美联储公开讲话：Inflation', date: '2026-12-02', time: '01:00', importance: 5, checkedAt: when, source: { name: '美联储官方日程', url: original, verifiedAt: when } }] } };
}
async function main() {
  const [dist, output] = process.argv.slice(2); fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
  try {
    for (const width of [390, 1280]) {
      const state = { userAgent: width === 390 ? iphone : '', watch: payload(), items: [item(3), { ...item(301), title: '中国Microsoft OpenAI合作' }], wallItems: [],
        cache: new Map(), requests: [], running: 0, maxRunning: 0, holdNext: false, fail: false };
      const { page, errors } = await mount(browser, dist, width, state);
      await sleep(450);
      assert.equal(state.requests.length, 0, 'hidden event titles were sent to the model');
      await page.locator('[data-target="watch-events"]').first().click();
      await until(() => state.cache.has(state.watch.monetaryPolicy.signals[0].title));
      const policy = page.locator('[data-watch-headline-ref="watch:policy:policy-1"]');
      assert((await policy.innerText()).startsWith('中文标题'));
      assert((await page.locator('.monetary-policy-signals').innerText()).includes('更新已延迟'));
      assert(!state.requests.includes('watch:event:event-1'), 'an offscreen event was translated');
      const calendar = page.locator('[data-watch-headline-ref="watch:calendar:calendar-1"]');
      await calendar.scrollIntoViewIfNeeded();
      await until(() => state.cache.has(state.watch.discovery.events[0].title));
      assert((await calendar.innerText()).startsWith('中文标题'), 'the English topic after a Chinese prefix was skipped');
      assert.equal(await calendar.locator('..').locator('.news-original-title').innerText(), state.watch.discovery.events[0].title);
      const event = page.locator('[data-watch-headline-ref="watch:event:event-1"]');
      await event.scrollIntoViewIfNeeded();
      await until(() => state.cache.has(state.watch.events[0].title));
      assert.equal(state.maxRunning, 1, 'news and event views launched simultaneous paid requests');
      const links = page.locator('[data-panel="watch-events"] [data-reading="chinese"]');
      assert.equal(await links.count(), 3, 'policy/calendar/official channel reading paths were not all covered');
      for (const link of await links.all()) {
        const href = await link.getAttribute('href');
        assert.equal(href.startsWith('googlechromes:'), width === 390);
        const url = new URL(href.replace(/^googlechromes:/, 'https:'));
        assert.equal(url.hostname, 'translate.google.com');
        assert.equal(url.searchParams.get('tl'), 'zh-CN');
        assert.equal(url.searchParams.get('u'), original);
        assert.equal(await link.getAttribute('rel'), 'noopener noreferrer');
        assert.equal(await link.getAttribute('referrerpolicy'), 'no-referrer');
      }
      const raw = await page.locator('[data-reading="original"]').first().getAttribute('href');
      assert.equal(raw, width === 390 ? original.replace('https:', 'googlechromes:') : original);
      await page.evaluate(() => document.addEventListener('click', e => {
        const a = e.target.closest('a[data-reading]');
        if (a) { e.preventDefault(); window.clickedReading = a.href; }
      }, true));
      await links.first().scrollIntoViewIfNeeded();
      await links.first().click();
      assert.equal(await page.evaluate(() => window.clickedReading), await links.first().getAttribute('href'));
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      const before = state.requests.length;
      await page.locator('[data-action="translate"]').click();
      assert.equal(await event.innerText(), state.watch.events[0].title);
      await sleep(300); assert.equal(state.requests.length, before);
      await page.locator('[data-action="translate"]').click();
      await sleep(400); assert.equal(state.requests.length, before, 'switching reused titles caused another model call');
      assert((await event.innerText()).startsWith('中文标题'));
      await sleep(5_100);
      state.staleWatch = true;
      await page.evaluate(() => window.dispatchEvent(new Event('online')));
      await sleep(600);
      assert((await event.innerText()).startsWith('中文标题'), 'refresh erased a completed event translation');
      assert.equal(state.requests.length, before);
      await page.screenshot({ path: path.join(output, `watch-reading-${width}.png`), fullPage: true });
      assert.deepEqual(errors, []);
      await page.close();
      console.log(JSON.stringify({ width, visibleEventsOnly: 'pass', policyCalendarAndWatch: 'pass', mixedTitle: 'pass',
        oneQueue: 'pass', chineseChromeAndOriginal: 'pass', sourceHealth: 'pass', toggleAndStaleCache: 'pass', realPaidCalls: 0 }));
    }
  } finally { await browser.close(); }
}
main().catch(e => { console.error(e); process.exitCode = 1; });
