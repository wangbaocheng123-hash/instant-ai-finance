/* Isolated visual regression: <dist-dir> <screenshots-dir>.
 * All APIs are synthetic; this test never contacts production or triggers collection/AI.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const [dist, output] = process.argv.slice(2);
  assert(dist && output, 'usage: node header-branding-browser.cjs <dist-dir> <screenshots-dir>');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
  });
  try {
    for (const width of [390, 1280]) {
      const page = await browser.newPage({ viewport: { width, height: 844 }, isMobile: width === 390, hasTouch: width === 390 });
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.route('http://instant-ai.test/**', async (route) => {
        const url = new URL(route.request().url());
        if (url.pathname.startsWith('/api/')) {
          const json = (value) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
          if (url.pathname === '/api/auth/status') return json({ required: true, authenticated: true, setup_required: false, username: 'amu', expires_at: null, session_days: 30 });
          if (url.pathname === '/api/status') return json({ items: { total: 0, unread: 0, saved: 0, last_seen: null }, sources: { total: 0, enabled: 0, errors: 0 }, collection: { running: false, last_result: null, mode: 'automatic', interval_seconds: 300 }, notifications: { pending: 0 }, database_path: '', library_path: '', latest_backup: null, retention: { ordinary_hours: 72, important_days: 5, critical_days: 7, archive_enabled: false } });
          if (url.pathname === '/api/items' || url.pathname === '/api/hot') return json([]);
          if (url.pathname === '/api/translation/status') return json({ enabled: true, provider: 'test', provider_label: '测试汉化', external: false, cached_titles: 0, used_characters_today: 0, daily_character_limit: null, remaining_characters_today: null, official_public_limit: null, target_language: 'zh-CN' });
          if (url.pathname === '/api/watch-events') return json({ events: [], counts: { total: 0, home: 0, zijin: 0, configured: 0, official_reachable: 0, signals_detected: 0, signals_delivered: 0 }, sync: { last_error: null, last_success_at: null } });
          if (url.pathname === '/api/model-mr/status') return json({ available: false, message: '测试环境未连接', counts: {} });
          if (url.pathname === '/api/blogger-library/status') return json({ available: false, message: '测试环境未连接', counts: { creators: 0, works: 0 } });
          return json({});
        }
        const requested = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
        const target = path.join(dist, requested);
        const contentTypes = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };
        return route.fulfill({ contentType: contentTypes[path.extname(target)] || 'application/octet-stream', body: fs.readFileSync(target) });
      });
      await page.goto('http://instant-ai.test/');
      await page.locator('.terminal-header').waitFor();
      await page.locator('.brand-mark').evaluate((image) => image.decode());
      const dimensions = await page.evaluate(() => {
        const header = document.querySelector('.terminal-header').getBoundingClientRect();
        const brand = document.querySelector('.brand-block').getBoundingClientRect();
        const tools = document.querySelector('.header-tools').getBoundingClientRect();
        const logo = document.querySelector('.brand-mark');
        return {
          header: { top: header.top, bottom: header.bottom, height: header.height },
          brand: { top: brand.top, bottom: brand.bottom },
          tools: { top: tools.top, bottom: tools.bottom },
          pageWidth: document.documentElement.clientWidth,
          scrollWidth: document.documentElement.scrollWidth,
          logoWidth: logo.naturalWidth,
          logoHeight: logo.naturalHeight,
        };
      });
      assert.deepEqual([dimensions.logoWidth, dimensions.logoHeight], [192, 192]);
      assert.equal(dimensions.scrollWidth, dimensions.pageWidth, `unexpected horizontal overflow at ${width}px`);
      if (width === 390) {
        assert(dimensions.header.height <= 46, `mobile header is ${dimensions.header.height}px high`);
        assert(Math.abs(dimensions.brand.top - dimensions.tools.top) <= 4, 'mobile brand and tools are not on the same row');
        assert(dimensions.brand.bottom <= dimensions.header.bottom && dimensions.tools.bottom <= dimensions.header.bottom);
      }
      await page.locator('.terminal-header').screenshot({ path: path.join(output, `instant-ai-header-${width}.png`) });
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
