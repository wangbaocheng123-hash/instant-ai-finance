/* Isolated official calendar UI: <compiled-esm-dir> <dist-dir> <screenshots-dir>.
 * Synthetic agendas only. No production data, real push, browser sessions or AI.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const [compiled, dist, output] = process.argv.slice(2);
  assert(compiled && dist && output);
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
  let checks = 0;
  try {
    for (const width of [390, 1280]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Shanghai' });
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.route('**/*', async (route) => {
        const url = new URL(route.request().url());
        assert.equal(url.origin, 'http://calendar.test', 'No external request is allowed');
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/styles.css"><main id="fixture"></main>' });
        if (url.pathname === '/styles.css') return route.fulfill({ contentType: 'text/css', body: fs.readFileSync(path.join(dist, 'styles.css')) });
        return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.join(compiled, path.basename(url.pathname))) });
      });
      await page.goto('http://calendar.test/');
      await page.evaluate(async () => {
        const { WatchEventsPanel } = await import('/WatchEventsPanel.js');
        const { FinancePanel } = await import('/FinancePanel.js');
        window.panel = new WatchEventsPanel();
        window.agenda = { key: 'fed:host-event:synthetic', title: '美联储生产率与就业专题会议', date: '2026-11-17', time: '01:00', importance: 5,
          checkedAt: '2026-11-16T12:00:00+00:00', source: { name: '旧金山联储官方会议议程', url: 'https://www.frbsf.org/news-and-media/events/2026/11/synthetic/', verifiedAt: '2026-11-16T12:00:00+00:00' } };
        window.response = { events: [{ event_key: 'home:timeline:instant-ai-future:fed:host-event:synthetic' }, { event_key: 'zijin:manual:instant-ai-future:fed:host-event:synthetic' }],
          counts: { total: 2, home: 1, zijin: 1, configured: 0, official_reachable: 0, signals_detected: 0, signals_delivered: 0 }, sync: null,
          discovery: { enabled: true, coverage: '覆盖美联储本月、次月日程及旧金山联储近期会议。', pendingDelivery: 0, failedDelivery: 0,
            sources: [{ key: 'fed-current', name: '美联储日程', state: 'ok', lastSuccessAt: '2026-11-16T12:00:00Z', lastError: '' }],
            events: [window.agenda, { ...window.agenda, key: 'fed:date-only:synthetic', title: '日期已公布的下一场会议', date: '2026-11-20', time: '' }] } };
        window.panel.render(window.response);
        const finance = new FinancePanel({ id: 'global', title: '全球即时财经', subtitle: 'GLOBAL FINANCIAL WIRE', accent: '#ffa100' });
        finance.render([{ id: 1, title: '【会议预告】11月17日01:00 美联储专题会议', thumbnail_url: '', url: window.agenda.source.url, summary: '',
          published_at: null, first_seen_at: '2026-11-16T12:00:00+00:00', importance_score: 95, trust_level: 5, topics: ['宏观政策'], entities: ['美联储'],
          event_type: '官方日程会议预告', source_count: 1, sources: ['美联储'], is_saved: false, is_read: false }]);
        document.querySelector('#fixture').append(window.panel.element, finance.element);
      });
      assert.equal(await page.locator('.official-agenda').count(), 2); checks++;
      assert.equal(await page.getByRole('heading', { name: '美联储生产率与就业专题会议', exact: true }).count(), 1); checks++;
      assert((await page.locator('.official-calendar-discovery').innerText()).includes('2026年11月17日 01:00（北京时间）')); checks++;
      assert((await page.locator('.official-calendar-discovery').innerText()).includes('官方当地日期 2026-11-20')); checks++;
      assert(!(await page.locator('.official-calendar-discovery').innerText()).includes('00:00')); checks++;
      assert.equal(await page.locator('.official-agenda a[data-reading="original"][target="_blank"][rel="noopener noreferrer"]').count(), 2); checks++;
      assert((await page.locator('[data-panel="global"]').innerText()).includes('发现于')); checks++;
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), width); checks++;
      await page.screenshot({ path: path.join(output, `official-calendar-${width}.png`), fullPage: true });
      await page.evaluate(() => {
        window.response.discovery.sources[0].state = 'error';
        window.panel.render(window.response);
      });
      assert((await page.locator('.official-calendar-discovery').innerText()).includes('上次核实记录')); checks++;
      assert.equal(await page.locator('.official-agenda').count(), 2); checks++;
      await page.evaluate(() => {
        window.response.discovery.events = [{ ...window.agenda, title: '<img src=x onerror=alert(1)>', source: { ...window.agenda.source, url: 'javascript:alert(1)' } }];
        window.panel.render(window.response);
      });
      assert.equal(await page.locator('.official-agenda img, .official-agenda a').count(), 0); checks++;
      assert((await page.locator('.official-agenda').innerText()).includes('<img src=x')); checks++;
      const unsafe = await page.evaluate(async () => {
        const { publicReadingUrl, sourceReadingLinks } = await import('/BrowserReading.js');
        const urls = ['javascript:alert(1)', 'http://127.0.0.1/private', 'https://10.0.0.1/private',
          'https://user:password@www.federalreserve.gov/private', 'https://www.federalreserve.gov/?api_key=synthetic',
          'https://www.federalreserve.gov/oauth/callback?code=synthetic', 'https://www.federalreserve.gov/#access_token=synthetic'];
        return urls.map(url => publicReadingUrl(url) === null && sourceReadingLinks(url) === null);
      });
      assert(unsafe.every(Boolean)); checks += unsafe.length;
      assert(await page.evaluate(async () => {
        const { publicReadingUrl } = await import('/BrowserReading.js');
        return publicReadingUrl('https://www.federalreserve.gov/public?code=1234') !== null;
      })); checks++;
      assert.deepEqual(errors, []); checks++;
      await page.close();
    }
  } finally { await browser.close(); }
  console.log(`${checks} official calendar browser checks passed at 390/1280px`);
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
