/* Isolated real-browser regression. No production requests or paid model calls.
 * Usage: NODE_PATH=<playwright install> node tests/model-mr-panel-browser.cjs <compiled-dir> <output-dir>
 * compiled-dir: ModelMrPanel.js (ESM imports ./api.js), api.js, styles.css.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const [compiled, output] = process.argv.slice(2);
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE || undefined });
  try {
    for (const width of [390, 1280]) {
      const page = await browser.newPage({ viewport: { width, height: 844 }, isMobile: width < 600, hasTouch: width < 600 });
      const errors = [];
      const requests = [];
      page.on('pageerror', error => errors.push(error.message));
      const categories = [{ id: 1, name: '行业主题', level: 1, parent_id: null, description: '分类回归样例', video_count: 30 },
        { id: 2, name: '科技总论与自主可控', level: 2, parent_id: 1, description: '已有关键词与关联作品', video_count: 30 }];
      const works = Array.from({ length: 30 }, (_, i) => ({ id: i + 1, title: `回归样例作品 ${i + 1}`, description: '', url: '', published_at: '2026-09-01', media_available: true, video_url: `/api/model-mr/works/${i + 1}/video`, has_video_text: true, has_interpretation: true, comment_count: 0, keywords: ['科技股', '长期研究'], keyword_revision: 'revision1', keyword_info: { categories: { '行业与板块': ['科技股'], '投资战略、战术与选股方法': ['长期研究'] }, keywords: ['科技股', '长期研究'] } }));
      let keywordSaves = 0;
      await page.route('http://127.0.0.1:19846/**', async route => {
        const url = new URL(route.request().url());
        const pathname = url.pathname;
        requests.push(pathname + url.search);
        const json = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
        if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/styles.css"><body><main id="fixture"></main><script type="module">import {ModelMrPanel} from '/ModelMrPanel.js'; const panel=new ModelMrPanel(); document.querySelector('#fixture').append(panel.element);panel.element.hidden=false;await panel.refresh();window.ready=true;</script>` });
        if (['/ModelMrPanel.js', '/ModelMrComments.js', '/api.js', '/styles.css'].includes(pathname)) return route.fulfill({ contentType: pathname.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(path.join(compiled, pathname.slice(1)), 'utf8') });
        if (pathname.endsWith('/status')) return json({ available: true, counts: { works: 30, media: 30 } });
        if (pathname.endsWith('/chat/config')) return json({ enabled: false, models: [], message: '测试不调用 AI' });
        if (pathname.endsWith('/thoughts')) return json({ categories, count: 2 });
        if (pathname.endsWith('/works')) {
          const offset = Number(url.searchParams.get('offset'));
          const limit = Number(url.searchParams.get('limit'));
          assert.equal(limit, 24);
          const q = url.searchParams.get('q') || '';
          const all = works.filter(work => !q || work.title.includes(q) || work.keywords.some(word => word.includes(q)));
          const items = all.slice(offset, offset + limit);
          return json({ items, count: items.length, total: all.length, offset, has_more: offset + items.length < all.length, category: categories[1], keywords: ['科技股'], links_available: true, message: '' });
        }
        if (/\/works\/\d+\/keywords$/.test(pathname)) {
          keywordSaves++;
          const payload = route.request().postDataJSON();
          assert.equal(payload.expected_revision, 'revision1');
          const info = { categories: payload.categories, keywords: Object.values(payload.categories).flat(), edited_by_owner: true };
          return json({ keyword_info: info, keywords: info.keywords, keyword_revision: 'revision2' });
        }
        if (/\/works\/\d+$/.test(pathname)) return json({ work: works[Number(pathname.split('/').pop()) - 1], video_text: { text: '已保存正式原文', official: true }, interpretation: { text: '已保存的解读感悟' }, comments: [], transcripts: [], comment_total: 0, stock_mentions: { items: [] }, capabilities: {} });
        if (pathname.endsWith('/video')) return route.fulfill({ status: 404, body: 'Synthetic fixture: no real media' });
        throw new Error(`Unexpected endpoint ${pathname}`);
      });
      await page.goto('http://127.0.0.1:19846/');
      await page.waitForFunction(() => window.ready);
      await page.locator('[data-model-tab="thoughts"]').click();
      assert(!requests.some(url => /thoughts\/\d+\/works/.test(url)), 'opening index must not load every category');
      await page.locator('[data-category-id="1"]').click();
      await page.locator('[data-category-id="2"]').click();
      await page.waitForFunction(() => document.querySelectorAll('.model-work-card').length === 24);
      assert.equal(await page.locator('video').count(), 0, 'media must be click-to-load');
      await page.locator('[data-model-action="thought-more"]').click();
      await page.waitForFunction(() => document.querySelectorAll('.model-work-card').length === 30);
      await page.locator('#modelMrThoughtQuery').fill('作品 30');
      await page.locator('#modelMrThoughtSearch button').click();
      await page.waitForFunction(() => document.querySelectorAll('.model-work-card').length === 1);
      await page.locator('[data-model-action="open-detail"][data-detail-tab="keywords"]').click();
      await page.locator('.model-keyword-panel').waitFor();
      assert.match(await page.locator('.model-keyword-panel').innerText(), /行业与板块/);
      await page.locator('[data-model-action="edit-keywords"]').click();
      await page.locator('[data-keyword-category="行业与板块"]').fill('半导体、AI');
      await page.locator('[data-model-action="save-keywords"]').click();
      await page.locator('[data-model-action="edit-keywords"]').waitFor();
      assert.equal(keywordSaves, 1);
      assert.match(await page.locator('.model-keyword-panel').innerText(), /半导体/);
      await page.locator('.model-detail-tabs [data-detail-tab="interpretation"]').click();
      assert.match(await page.locator('.model-saved-interpretation').innerText(), /已保存的解读感悟/);
      await page.locator('.model-detail-tabs [data-detail-tab="video"]').click();
      assert.equal(await page.locator('video').count(), 1);
      assert.equal(await page.locator('video').getAttribute('preload'), 'metadata');
      assert.equal(await page.locator('video').getAttribute('autoplay'), null);
      assert(await page.locator('.model-thought-detail').count(), 'shared player must remain inside selected category');
      await page.locator('.model-detail-tabs [data-detail-tab="keywords"]').click();
      await page.screenshot({ path: path.join(output, `keywords-${width}.png`), fullPage: true });
      await page.locator('[data-model-action="thought-back"]').click();
      await page.locator('[data-category-id="2"]').waitFor();
      await page.locator('[data-model-action="thought-back"]').click();
      await page.locator('[data-category-id="1"]').waitFor();
      assert(!requests.some(url => /transcribe|\/chat$|collect|preview-keywords/.test(url)), 'no AI or collection endpoint');
      assert.deepEqual(errors, []);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      assert.equal(overflow, false, 'no horizontal overflow');
      console.log(JSON.stringify({ width, categories: 'pass', pagination: 'pass', keywordEdit: 'pass', player: 'pass', noPaidCalls: true }));
      await page.close();
    }
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
