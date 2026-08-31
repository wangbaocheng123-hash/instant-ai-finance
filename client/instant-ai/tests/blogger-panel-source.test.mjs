import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const projectRoot = new URL('../', import.meta.url);
const readSource = (relativePath) => readFile(new URL(relativePath, projectRoot), 'utf8');

test('blogger API uses only the four frozen owner read paths', async () => {
  const api = await readSource('src/instant-ai/api.ts');
  assert.match(api, /'\/api\/blogger-library\/status'/u);
  assert.match(api, /'\/api\/blogger-library\/creators'/u);
  assert.match(api, /blogger-library\/creators\/\$\{encodeURIComponent\(creatorId\)\}\/works/u);
  assert.match(api, /blogger-library\/works\/\$\{encodeURIComponent\(workKey\)\}/u);
  assert.doesNotMatch(api, /blogger.*(?:transcribe|approve|doubao)/iu);
});

test('blogger panel stays isolated and never exposes a paid ASR action', async () => {
  const panel = await readSource('src/instant-ai/BloggerPanel.ts');
  const apiCalls = [...panel.matchAll(/instantApi\.([A-Za-z0-9_]+)/gu)].map((match) => match[1]);
  assert.deepEqual(
    [...new Set(apiCalls)].sort(),
    ['bloggerCreatorWorks', 'bloggerCreators', 'bloggerLibraryStatus', 'bloggerWork'],
  );
  assert.match(panel, /awaiting_asr_approval/u);
  assert.match(panel, /系统不会自动调用付费识别，也不会自动产生费用/u);
  assert.match(panel, /客户端不会从传输状态自行推断/u);
  assert.doesNotMatch(panel, /ModelMr|FinanceItem|<video|<img|cover_url/u);
  assert.doesNotMatch(panel, /data-blogger-action=["'][^"']*(?:asr|transcrib|approve|doubao)/iu);
});

test('main shell exposes an eighth independent mobile entry', async () => {
  const [app, styles] = await Promise.all([
    readSource('src/instant-ai/InstantFinanceApp.ts'),
    readSource('src/instant-ai/styles.css'),
  ]);
  assert.match(app, /data-target="blogger-library"/u);
  assert.match(app, /new BloggerPanel\(\)/u);
  assert.match(app, /bloggerPanel\.refresh\(\)/u);
  assert.ok(app.indexOf('data-target="model-mr"') < app.indexOf('data-target="blogger-library"'));
  assert.ok(app.indexOf('grid.append(this.modelMrPanel.element)') < app.indexOf('grid.append(this.bloggerPanel.element)'));
  assert.match(styles, /repeat\(8, minmax\(44px, 1fr\)\)/u);
});
