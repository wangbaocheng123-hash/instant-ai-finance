/* Synthetic API only. No cloud, credentials or paid calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const [compiled, output] = process.argv.slice(2);
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
  try {
    for (const width of [390, 1280]) {
      const page = await browser.newPage({ viewport: { width, height: 844 }, isMobile: width === 390, hasTouch: width === 390 });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      let accepts = false, writes = 0, keywordRequests = 0;
      page.on('dialog', d => accepts ? d.accept() : d.dismiss());
      const processing = { enabled: false, failures: 0, daily_call_limit: 20, max_video_minutes: 20,
        speech_configured: true, keywords_configured: false, items: [] };
      const work = { id: 1, title: '模拟新视频', description: '', published_at: '2026-09-04',
        media_available: false, has_video_text: true, keywords: [], keyword_revision: 'r1',
        keyword_info: { categories: { '行业与板块': [] }, keywords: [] }, comment_count: 0 };
      await page.route('http://127.0.0.1:19849/**', async route => {
        const p = new URL(route.request().url()).pathname;
        const json = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
        if (p === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/styles.css"><div id="root"></div><script type="module">import {ModelMrPanel} from '/ModelMrPanel.js';const p=new ModelMrPanel();document.querySelector('#root').append(p.element);p.element.hidden=false;await p.refresh();window.ready=true;</script>` });
        if (['/ModelMrPanel.js', '/ModelMrComments.js', '/api.js', '/styles.css'].includes(p)) return route.fulfill({ contentType: p.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(path.join(compiled, p.slice(1)), 'utf8') });
        if (p.endsWith('/status')) return json({ available: true, counts: { works: 1, media: 0 } });
        if (p.endsWith('/chat/config')) return json({ enabled: false, models: [] });
        if (p.endsWith('/thoughts')) return json({ categories: [] });
        if (p.endsWith('/works')) return json({ items: [work], count: 1, total: 1, offset: 0, has_more: false });
        if (p === '/api/model-mr/processing') {
          if (route.request().method() === 'POST') {
            const body = route.request().postDataJSON();
            assert.equal(body.confirm_billing, true); processing.enabled = body.enabled; writes++;
          }
          return json(processing);
        }
        if (p.endsWith('/extract-keywords')) {
          const body = route.request().postDataJSON();
          assert.equal(body.expected_revision, 'r1'); assert.equal(body.confirm_billing, true); keywordRequests++;
          processing.items = [{ id: 1, work_id: 1, state: 'configuration', phase: 'keywords', message: '缺少豆包文本配置', updated: 1 }];
          return json({ message: '等待串行处理' });
        }
        if (p === '/api/model-mr/works/1') return json({ work, video_text: { text: '科技股原文', official: true, source: 'doubao-auto-unreviewed' }, interpretation: {}, comments: [], comment_total: 0, transcripts: [], stock_mentions: { items: [] }, capabilities: { save_video_text: true } });
        throw Error(`Unexpected API ${p}`);
      });
      await page.goto('http://127.0.0.1:19849/');
      await page.waitForFunction(() => window.ready);
      await page.locator('[data-model-action="processing"]').click();
      await page.locator('[data-model-action="toggle-processing"]').waitFor();
      assert.match(await page.locator('.model-processing').innerText(), /关键词模型：未配置/);
      await page.locator('[data-model-action="toggle-processing"]').click();
      assert.equal(writes, 0, 'cancelled consent must not enable processing');
      accepts = true;
      await page.locator('[data-model-action="toggle-processing"]').click();
      await page.getByRole('button', { name: '暂停自动处理' }).waitFor();
      assert.equal(writes, 1);
      await page.locator('[data-model-action="open-detail"][data-detail-tab="text"]').click();
      await page.locator('#model-video-text-1').waitFor();
      assert.match(await page.locator('.model-text-source').innerText(), /尚未人工核对/);
      await page.locator('#model-video-text-1').fill('尚未保存的编辑草稿');
      await page.locator('[data-model-action="processing"]').click();
      assert.equal(await page.locator('#model-video-text-1').inputValue(), '尚未保存的编辑草稿');
      await page.locator('.model-detail-tabs [data-detail-tab="keywords"]').click();
      await page.locator('[data-model-action="extract-keywords"]').click();
      await page.waitForFunction(() => document.body.innerText.includes('等待串行处理'));
      assert.equal(keywordRequests, 1);
      await page.locator('[data-model-action="processing"]').click();
      await page.getByText('作品 1 · 关键词：缺少豆包文本配置', { exact: false }).waitFor();
      await page.locator('.model-processing').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `auto-processing-${width}.png`), fullPage: true });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ width, consent: 'pass', status: 'pass', draftPreserved: true, keywordQueue: 'pass', realPaidCalls: 0 }));
      await page.close();
    }
  } finally { await browser.close(); }
}
main().catch(e => { console.error(e); process.exitCode = 1; });
