# 即时 AI 前端源代码

这是仓库内唯一需要修改和构建的即时 AI 前端目录。它从许可证隔离的 World Monitor `v2.5.23` 分叉中提取，继续按 AGPL-3.0-only 发布；来源和固定提交见 `NOTICE.txt`。

```powershell
npm ci
npm run build
```

构建输出在 `dist/`。正式发布时将 `app.js`、`audio-hls.light.js`、`hls-js-license.txt`、`styles.css`、`index.html`、`manifest.webmanifest`、`sw.js`、`app-icon-192.png`、`app-icon-512.png` 和 `apple-touch-icon.png` 同步到 `product/instant_ai/static/`。顺序音频的HLS后备脚本与完整许可证必须一起同步；原生支持HLS的手机不需加载该后备脚本。不要在 `dist/` 或 `product/instant_ai/static/` 直接修改压缩后的代码。
