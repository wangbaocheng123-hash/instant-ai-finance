# World Monitor 全球财经展示分叉记录

## 结论

即时 AI 不原样运行 World Monitor，也不把其数据库或云服务当主底座。采用独立 AGPL 分叉保留上游历史和高密度终端设计，并用一个只连接本机 API 的窄化运行入口替换原始大而全应用。

## 固定来源

- 官方仓库：`https://github.com/koala73/worldmonitor.git`
- 发布标签：`v2.5.23`
- 上游提交：`e51058e1765ef2f0c83ccb1d08d984bc59d23f10`
- 上游只读目录：`upstream/WorldMonitor`
- 产品分叉目录：`forks/InstantAI-WorldMonitor`
- 产品分叉提交：`daa3b2eb5cd6e301284c6b4bef4c5fd9877a893e`
- 许可证：`AGPL-3.0-only`

## 上游源码证据

- `src/App.ts:282-299`：原应用统一组织大量 panel 和管理器，证明模块化面板是可借鉴的展示模式。
- `src/main.ts:197`、`src/services/runtime.ts:151`：生产路径包含 `api.worldmonitor.app` 云端路由；即时 AI 运行入口不得继承该依赖。
- `src/components/LiveNewsPanel.ts:9-54`：存在 YouTube player、videoId 和 HLS 字段；`1294-1324` 还包含 YouTube 登录入口。
- `src/components/LiveWebcamsPanel.ts:19-56`、`214-226`：包含摄像头目录和 iframe 构造逻辑。
- `api/youtube/embed.js`、`api/youtube/live.js`、`src/services/live-news.ts`：视频代理与直播发现链路完整存在，因此仅靠界面隐藏不足以满足“不要自动视频”的边界。

## 分叉实际采用

- `src/main.ts`：唯一启动 `InstantFinanceApp`。
- `src/instant-ai/InstantFinanceApp.ts`：顶部“即时 + 全球热点”双滚动栏、12 个财经频道、搜索、来源和详情交互；即时按时间排列，热点按近 72 小时的多来源数、重要度和新鲜度综合排序；频道采用单页状态直切，桌面导航与手机底栏共用选中状态，不再用平滑滚动定位面板；只显示自动实时采集状态，不提供手动采集按钮。
- `src/instant-ai/FinancePanel.ts`：白色左图右文新闻面板及中文译文/英文原题双标题。
- `src/instant-ai/api.ts`：只使用相对 `/api`，由本机服务承接。
- `src/instant-ai/styles.css`：白底黑字、单频道整页新闻面板、缩略图和响应式布局；汉化、来源、自动实时采集合并为 32px 高的紧凑状态工具条。
- `vite.config.ts`：开发代理固定到 `127.0.0.1:18765`，生产产物为稳定的 `app.js` 和 `styles.css`。
- `index.html`：CSP 将 `connect-src` 限制为 self，并设置 `media-src 'none'`、`frame-src 'none'`、`object-src 'none'`。

## 明确删除/停用

- YouTube embed/live API、HLS 测试、直播新闻与直播摄像头组件。
- live channel 独立窗口、桌面 YouTube 登录 capability 和相关说明。
- 原始地图、航空/船舶、医疗、火灾、网络威胁等非财经运行入口。
- World Monitor 账户、企业版、分析、错误上报或云 API 的运行连接。

旧模块仍可在上游提交历史中审计，但不进入自定义 TypeScript 编译入口和部署产物。

## 验证

- `npm run build`：TypeScript 严格检查与 Vite 生产构建通过；运行产物约 23.9 KiB JS 与 14.0 KiB CSS。
- API 首轮扩展采集：20 来源、抓取 689 条、新增 642、更新 47、错误 0。
- 截至第 37 次正式采集：数据库 947 条，20/20 来源健康，错误 0。
- 本地浏览器：12 个频道可定位；“黄金”搜索返回 23 条；新闻详情展示 4 份 SHA-256 证据；控制台错误 0。
- 标题汉化：开关可恢复英文原题；列表和详情均显示中文译文加英文原题，重复 API 请求命中 H 盘缓存而不增加用量。
- 自动采集检查：0.6.0 服务启动即采，之后每 5 分钟持续更新；第 43 次正式采集 970 条、20/20 来源健康、错误 0，页面不存在“立即采集”。
- 新闻原图检查：Feed 图片之外，按需读取发布方正文图片元数据和 Google News 同条新闻预览；当前缓存 65 张真实图片，最新 10 条中 9 条返回 `article`，缺图条目明确显示“暂无新闻原图”；`video`/`audio`/`iframe` 为 0。
- 桌面壳检查：桌面快捷方式打开的 Edge 应用窗口实测 1240×820、居中、非最大化，复用旧窗口时也会恢复该尺寸。
- 字体可读性检查：新闻标题由 12px 放大到 15px，来源与发布时间由 8px 放大到 11px，英文原题、标签、搜索结果、详情及全局辅助文字同步提高；生产构建通过，本机服务已返回新样式文件。
- 全球热点检查：顶部双栏在 1240×820 客户端内各占一半，各从 16 条内容复制为 32 个按钮实现无缝滚动；热点内容来自同一套本机全球采集库，点击沿用证据详情；窄屏改为上下叠放，浏览器控制台错误 0。
- 0.7.1 频道直切检查：桌面先后点击华尔街和黄金、手机底栏点击华尔街后，页面始终只保留一个可见频道，`scrollY=0`，左侧导航与手机底栏的 `aria-current` 同步；顶部三项状态工具实测为 32×209px。TypeScript/Vite 构建与 14 项单元测试通过。

## 许可证与内容边界

分叉及其编译产物按 AGPL-3.0-only 管理，不与根仓库源码混合历史。Reuters、Bloomberg、FT、WSJ 和投行名称目前用于公开搜索 Feed 的标题发现与回链，不等同于授权全文或专业实时终端；任何付费墙都不绕过。
