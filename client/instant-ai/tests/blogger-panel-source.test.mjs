import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const projectRoot = new URL('../', import.meta.url);
const readSource = (relativePath) => readFile(new URL(relativePath, projectRoot), 'utf8');

test('blogger API exposes the owner media workspace without a public write path', async () => {
  const api = await readSource('src/instant-ai/api.ts');
  assert.match(api, /'\/api\/blogger-library\/status'/u);
  assert.match(api, /'\/api\/blogger-library\/creators'/u);
  assert.match(api, /blogger-library\/creators\/\$\{encodeURIComponent\(creatorId\)\}\/works/u);
  assert.match(api, /blogger-library\/works\/\$\{encodeURIComponent\(workKey\)\}/u);
  assert.match(api, /saveBloggerTitle/u);
  assert.match(api, /saveBloggerVideoText/u);
  assert.match(api, /transcribeBloggerWork/u);
  assert.match(api, /doubao-transcribe/u);
  assert.match(api, /method: 'POST'/u);
});

test('blogger panel reuses the owner player and requires explicit paid ASR confirmation', async () => {
  const panel = await readSource('src/instant-ai/BloggerPanel.ts');
  const apiCalls = [...panel.matchAll(/instantApi\.([A-Za-z0-9_]+)/gu)].map((match) => match[1]);
  assert.deepEqual(
    [...new Set(apiCalls)].sort(),
    [
      'bloggerCreatorWorks', 'bloggerCreators', 'bloggerLibraryStatus', 'bloggerWork',
      'saveBloggerTitle', 'saveBloggerVideoText', 'transcribeBloggerWork',
    ],
  );
  assert.match(panel, /createElement\('video'\)/u);
  assert.match(panel, /video\.controls = true/u);
  assert.match(panel, /video\.playsInline = true/u);
  assert.match(panel, /视频原文/u);
  assert.match(panel, /豆包识别文字/u);
  assert.match(panel, /window\.confirm\('豆包识别会提取本地视频音频并按音频时长调用付费接口/u);
  assert.match(panel, /本人互动/u);
  assert.match(panel, /评论排行/u);
  assert.match(panel, /全部评论/u);
  assert.match(panel, /awaiting_asr_approval/u);
  assert.match(panel, /豆包只在主人确认后调用/u);
  assert.doesNotMatch(panel, /FinanceItem|<img|cover_url/u);
});

test('creator switching and add creator stay anchored to the Beijing collector', async () => {
  const panel = await readSource('src/instant-ai/BloggerPanel.ts');
  assert.match(panel, /blogger-creator-switch/u);
  assert.match(panel, /\+ 新增博主/u);
  assert.match(panel, /https:\/\/collector\.amuyeye\.com\//u);
  assert.doesNotMatch(panel, /collector\.amuyeye\.com\/collector/u);
  assert.match(panel, /dataset\.creatorId = creator\.creator_id/u);
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
