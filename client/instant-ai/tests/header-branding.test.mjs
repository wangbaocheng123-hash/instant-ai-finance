import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const projectRoot = new URL('../', import.meta.url);
const readText = (relativePath) => readFile(new URL(relativePath, projectRoot), 'utf8');

const readPngSize = async (relativePath) => {
  const image = await readFile(new URL(relativePath, projectRoot));
  assert.equal(image.subarray(1, 4).toString('ascii'), 'PNG');
  return [image.readUInt32BE(16), image.readUInt32BE(20)];
};

test('the supplied Instant AI artwork is used by the shell and owner login', async () => {
  const app = await readText('src/instant-ai/InstantFinanceApp.ts');
  assert.equal((app.match(/src="\/app-icon-192\.png"/gu) || []).length, 2);
  assert.doesNotMatch(app, /class="brand-mark">即</u);
  assert.match(app, /data-compact-label="实时采集"/u);
  assert.match(app, /dataset\.compactLabel = status\.collection\.running \? '采集中' : '实时采集'/u);
});

test('mobile header keeps brand and collection tools in one compact row', async () => {
  const styles = await readText('src/instant-ai/styles.css');
  assert.match(styles, /grid-template-columns: auto minmax\(0, 1fr\)/u);
  assert.match(styles, /min-height: 44px/u);
  assert.match(styles, /#collectionMode::after \{ content: attr\(data-compact-label\)/u);
  assert.doesNotMatch(styles, /\.login-brand > span/u);
});

test('PWA and iPhone metadata use the new raster artwork at exact sizes', async () => {
  const index = await readText('index.html');
  const manifest = JSON.parse(await readText('public/manifest.webmanifest'));
  const worker = await readText('public/sw.js');

  assert.match(index, /app-icon-192\.png/u);
  assert.match(index, /apple-touch-icon\.png/u);
  assert.deepEqual(manifest.icons.map(({ src, sizes, type }) => ({ src, sizes, type })), [
    { src: '/app-icon-192.png', sizes: '192x192', type: 'image/png' },
    { src: '/app-icon-512.png', sizes: '512x512', type: 'image/png' },
  ]);
  assert.match(worker, /instant-ai-shell-v0\.20\.0/u);
  for (const asset of ['/app-icon-192.png', '/app-icon-512.png', '/apple-touch-icon.png']) {
    assert.match(worker, new RegExp(asset.replaceAll('.', '\\.')));
  }
  assert.deepEqual(await readPngSize('public/app-icon-192.png'), [192, 192]);
  assert.deepEqual(await readPngSize('public/app-icon-512.png'), [512, 512]);
  assert.deepEqual(await readPngSize('public/apple-touch-icon.png'), [180, 180]);
});
