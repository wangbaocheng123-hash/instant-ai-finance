# RSSHub 测试结果

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`）。

## 本子任务结果

状态：`NOT_ATTEMPTED_BY_THIS_SUBTASK`。

本子任务只进行固定提交归档快照的静态代码考古：

- 未安装 Node/pnpm 依赖；
- 未运行 `pnpm build`、`pnpm dev`、`pnpm start` 或 Vitest；
- 未启动 RSSHub、Redis、browserless、Docker、Worker 或 Vercel；
- 未请求任何 RSSHub URL 或第三方来源；
- 未写入 `upstream`、`experiments`、`product` 或 H 盘业务数据；
- 没有启动截图或运行日志。

因此以下项目均为 `NOT_TESTED`：

| 验证项 | 结果 |
|---|---|
| 成功启动 | `NOT_TESTED` |
| 成功抓到数据 | `NOT_TESTED` |
| 成功保存数据 | `NOT_TESTED`；源码也没有业务持久化 |
| 成功展示 | `NOT_TESTED` |
| 成功调用 API | `NOT_TESTED` |
| Windows 直接运行 | `NOT_TESTED` |
| Docker 运行 | `NOT_TESTED`；Docker 当前未安装且需批准 |
| 磁盘增量 | 0（本子任务没有安装/构建产物；仅新增研究 Markdown） |
| 大量无关依赖 | 未产生；`node_modules` 不存在 |

## 静态测试基础证据

固定提交包含 Vitest 测试基础：

- `vitest.config.ts`：Node 测试、coverage、10 秒默认 timeout；Route 源码排除在 coverage 统计外。
- `vitest.workers.config.ts`：Cloudflare Worker/KV 测试配置。
- `lib/app.test.ts`：主页、request rewriter 和可选 full-route examples；验证 RSS 字段和单 Feed
  guid 唯一性。
- `lib/index.test.ts`：用 mock 验证单进程与 cluster 启动配置。
- `lib/middleware/*.test.ts`、`lib/utils/cache/*.test.ts`：中间件和缓存单元测试。

这些文件存在不等于测试在本机通过，验证状态为 `SOURCE_VERIFIED_TEST_DESIGN_ONLY`。

## 快照与预期构建条件

- 快照文件：约 6,805；15.10 MiB。
- `.git`：不存在。
- `node_modules`：不存在。
- `dist`、`dist-lib`、`dist-worker`：不存在。
- `assets/build`：只有 `.gitkeep`。
- `package.json` 要求 Node `^22.22.2 || ^24.15.0`、pnpm `10.34.5`。

静态推断：`pnpm start` 会尝试执行 `dist/index.mjs`，所以当前快照必须先安装依赖并构建；
`pnpm dev` 使用源代码惰性 registry，可能是更轻的首次启动路径，但仍需安装依赖。

## 后续受控运行验收建议

1. 获得依赖下载授权，在 `experiments/RSSHub-lab` 创建实验副本。
2. 记录实验前后目录大小、pnpm store 变化和网络下载量。
3. 显式本机绑定，先测 `/healthz`。
4. 先测无凭据、无浏览器的公开财经 Route；每条记录状态码、解析条数、guid、日期和缓存。
5. 测试关闭进程后端口、日志和生成物清理。
6. 浏览器、Redis、Docker 和任何站点 Cookie 分开审批，不能为一次轻量验证全装。

运行验证优先级仍遵守总指令：TrendRadar、changedetection 后，RSSHub 属第二优先级。
