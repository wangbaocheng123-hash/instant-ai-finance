/* Isolated blogger queue UI regression: <compiled-dir> <screenshots-dir>. */
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
      const page = await browser.newPage({ viewport: { width, height: 900 }, isMobile: width === 390, hasTouch: width === 390 });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('dialog', dialog => dialog.accept());
      let enabled = false;
      let repairAt = 0;
      let processingPosts = 0;
      const creator = {
        creator_id: '11111111-2222-4333-8444-555555555555', display_name: '贵族之路', platform: 'douyin',
        work_count: 1, latest_published_at: '2026-09-07T08:00:00Z', latest_captured_at: '2026-09-07T08:05:00Z',
        status_counts: { works: 1, transferring: 0, awaiting_asr_approval: 1, ready: 0, failed: 0 },
      };
      const work = {
        work_key: 'a'.repeat(64), creator_id: creator.creator_id, source_work_id: 'work-1', platform: 'douyin', work_type: 'video',
        title: '贵族之路最新视频', description: '', source_url: 'https://www.douyin.com/video/1',
        published_at: '2026-09-07T08:00:00Z', captured_at: '2026-09-07T08:05:00Z',
        transfer: { status: 'verified', source_revision: 1, received_at: '2026-09-07T08:05:00Z', media_expected: 1, media_received: 1, comments_expected: 1, comments_received: 1 },
        processing_status: 'awaiting_asr_approval', media_available: true, video_url: '/api/blogger-library/works/' + 'a'.repeat(64) + '/video',
        has_video_text: false, has_interpretation: false, keywords: [], keyword_info: { categories: {}, keywords: [], model: '', schema_version: '', confirmed_at: '', stale: false, edited_by_owner: false }, keyword_revision: 'r0', comment_count: 0,
      };
      const detail = () => ({ ...work, has_video_text: Boolean(repairAt && Date.now() - repairAt > 1_000),
        keywords: repairAt && Date.now() - repairAt > 1_000 ? ['黄金'] : [],
        video_text: { text: repairAt && Date.now() - repairAt > 1_000 ? '自动识别后的原文' : '', official: false, source: repairAt ? 'doubao-auto-unreviewed' : '', updated_at: '' },
        transcripts: [], comments: [], interpretation: { text: '', updated_at: '' }, stock_mentions: { total_comments: 0, stock_count: 0, items: [], uncertain: [], method: '', api_used: false, message: '' }, comment_total: 0,
        capabilities: { video: true, save_title: true, save_video_text: true, transcribe_video: false, doubao_asr: true, comments: false },
      });
      const processing = () => {
        const elapsed = repairAt ? Date.now() - repairAt : 0;
        const state = !repairAt ? null : elapsed > 1_000 ? 'done' : 'queued';
        const item = state ? [{
          id: 1, work_key: work.work_key, title: work.title, creator_name: creator.display_name, kind: 'pipeline', automatic: false,
          mode: '手动补做', state, phase: state === 'done' ? 'complete' : 'asr', message: state === 'done' ? '处理完成' : '等待串行处理', updated: Math.floor(Date.now() / 1000),
          steps: state === 'done'
            ? { asr: { state: 'done', message: '已完成或复用已有原文' }, keywords: { state: 'done', message: '已完成或复用已有关键词' } }
            : { asr: { state: 'queued', message: '等待串行处理' }, keywords: { state: 'waiting', message: '等待视频原文' } },
        }] : [];
        return { enabled, failures: 0, enabled_since: enabled ? 1 : 0, last_reconciled: enabled ? Math.floor(Date.now() / 1000) : 0,
          worker_running: true, worker_last_seen: Math.floor(Date.now() / 1000), daily_call_limit: 20, max_video_minutes: 20,
          speech_configured: true, keywords_configured: true,
          summary: { queued: state === 'queued' ? 1 : 0, running: 0, done: state === 'done' ? 1 : 0, configuration: 0, review: 0, quota: 0, conflict: 0, total: item.length }, items: item };
      };

      await page.route('http://127.0.0.1:19851/**', async route => {
        const pathname = new URL(route.request().url()).pathname;
        const json = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
        if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/styles.css"><div id="root"></div><script type="module">import {BloggerPanel} from "/BloggerPanel.js";const panel=new BloggerPanel();document.querySelector("#root").append(panel.element);panel.element.hidden=false;await panel.refresh();window.ready=true;</script>' });
        if (['/BloggerPanel.js', '/ModelMrComments.js', '/api.js', '/styles.css'].includes(pathname)) return route.fulfill({ contentType: pathname.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(path.join(compiled, pathname.slice(1)), 'utf8') });
        if (pathname === '/api/blogger-library/status') return json({ available: true, module: 'blogger-library', mode: 'owner-mobile-library', message: 'ok', counts: { creators: 1, works: 1, transferring: 0, awaiting_asr_approval: 1, ready: 0, failed: 0 } });
        if (pathname === '/api/blogger-library/creators') return json({ items: [creator], count: 1 });
        if (pathname.endsWith('/works') && pathname.includes('/creators/')) return json({ creator, items: [{ ...work, has_video_text: Boolean(repairAt && Date.now() - repairAt > 1_000), keywords: repairAt && Date.now() - repairAt > 1_000 ? ['黄金'] : [] }], count: 1 });
        if (pathname === `/api/blogger-library/works/${work.work_key}`) return json(detail());
        if (pathname === '/api/blogger-library/processing') {
          if (route.request().method() === 'POST') {
            const body = route.request().postDataJSON(); assert.equal(body.confirm_billing, true); enabled = body.enabled; processingPosts += 1;
          }
          return json(processing());
        }
        if (pathname.endsWith('/process')) {
          const body = route.request().postDataJSON(); assert.equal(body.confirm_billing, true); repairAt = Date.now();
          return json({ job_id: 1, state: 'queued', message: '等待串行处理' });
        }
        if (pathname.endsWith('/video')) return route.fulfill({ status: 404, body: '' });
        throw Error(`Unexpected API ${pathname}`);
      });

      await page.goto('http://127.0.0.1:19851/');
      await page.waitForFunction(() => window.ready);
      await page.locator('[data-blogger-action="open-creator"]').first().click();
      assert.match(await page.locator('.blogger-processing').innerText(), /自动处理已关闭/);
      await page.getByRole('button', { name: '手动开启' }).click();
      await page.getByText('自动处理已开启', { exact: true }).waitFor();
      assert.equal(processingPosts, 1);
      await page.locator('[data-blogger-action="open-work"]').click();
      await page.getByRole('button', { name: '一键补做原文 + AI关键词' }).click();
      await page.getByText('视频原文', { exact: true }).last().waitFor();
      assert.match(await page.locator('.blogger-processing').innerText(), /等待串行处理/);
      await page.waitForFunction(() => document.querySelector('.blogger-processing')?.textContent?.includes('处理完成'), null, { timeout: 7_000 });
      await page.getByRole('button', { name: '视频原文' }).click();
      await page.locator('#blogger-video-text').waitFor();
      assert.equal(await page.locator('#blogger-video-text').inputValue(), '自动识别后的原文');
      await page.locator('.blogger-processing').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `blogger-processing-${width}.png`), fullPage: true });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ width, switch: 'pass', manualRepair: 'pass', polling: 'pass', steps: 'pass', realPaidCalls: 0 }));
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
