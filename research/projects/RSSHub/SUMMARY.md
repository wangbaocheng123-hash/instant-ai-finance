# RSSHub 静态研究摘要

## 证据范围

| 项目元数据 | 记录 | 验证状态 |
|---|---|---|
| 正式仓库 | `DIYgod/RSSHub` / `https://github.com/DIYgod/RSSHub.git` | `UPSTREAM_LOCK_VERIFIED` |
| 默认分支 | `master` | `UPSTREAM_LOCK_VERIFIED` |
| 固定提交 | `5151c3233bc7bacfaecc6e4f01aba2b60022d683` | `OFFICIAL_ARCHIVE_DECLARED_COMMIT` |
| 下载日期 | `2026-08-23` | `UPSTREAM_LOCK_VERIFIED` |
| 下载形态 | `OFFICIAL_ARCHIVE_SNAPSHOT` | `SOURCE_VERIFIED` |
| 源码位置 | `upstream/RSSHub-snapshot` | `SOURCE_VERIFIED` |
| 许可证 | `AGPL-3.0`；`LICENSE` 与 `package.json.license` 一致 | `SOURCE_VERIFIED` |
| 主要语言 | TypeScript/TSX，Node.js ESM | `SOURCE_VERIFIED` |
| 包版本/标签 | `package.json.version = 1.0.0`；Git 标签不可从无 `.git` 快照确认 | `SOURCE_VERIFIED / TAG_UNVERIFIED` |
| 上游活跃度 | 本快照结构和依赖较新，但无 Git 历史，不能复核提交频率 | `UNVERIFIED` |
| Windows 说明 | 快照 README 仅指向外部部署文档，无专项 Windows 手册；源码 scripts 静态上可跨平台 | `SOURCE_VERIFIED / RUNTIME_UNVERIFIED` |
| 快照事实 | 约 6,805 个文件、15.10 MiB；无 `.git`、`node_modules`、`dist*`；`assets/build` 只有 `.gitkeep` | `SOURCE_VERIFIED` |

所有技术判断均为 `SOURCE_VERIFIED`，除非明确写成 `DOC_ONLY` 或 `UNVERIFIED`；没有运行验证。

该快照由 GitHub 官方固定提交归档取得，但没有 Git 元数据，所以不能算完成轻量
克隆，也不能从快照本身复核提交、标签或历史活跃度。

## 项目解决的问题

RSSHub 把大量网站、平台 API 和 HTML 页面转换为统一的 RSS/Atom/JSON Feed。
它本质上是请求驱动的数据源适配服务：客户端请求一条路由，路由处理器抓取上游，
返回统一 `Data`/`DataItem`，中间件再做缓存、过滤、全文化、可选 OpenAI 处理与格式渲染。

证据：

- 源码：`lib/index.ts`；标识符：`serve({ fetch: app.fetch })`；结论：Node HTTP 入口。
- 源码：`lib/app-bootstrap.tsx`；标识符：Hono 中间件栈与 `app.route('/', registry)`；结论：Web 后端是 Hono。
- 源码：`lib/registry-helpers.ts`；标识符：`registerRssRoutes()`、`wrappedHandler`；结论：路由处理器按请求执行并把结果写入 `ctx.data`。
- 源码：`lib/types.ts`；标识符：`Data`、`DataItem`、`Route`；结论：统一 Feed 数据合同和扩展元数据。
- 验证状态：`SOURCE_VERIFIED`。

## 目标用户与核心能力

目标用户是需要把“不提供 RSS 或 RSS 不完整”的来源接入阅读器、监控器或下游系统的
个人与服务运营者。核心能力包括：

1. 大规模 Route/Namespace 数据源适配；
2. API、HTML 抓取、可选 Playwright 浏览器抓取；
3. 内存、Redis、HTTP、Cloudflare KV 缓存；
4. RSS、Atom、JSON Feed、RSS3/UMS 输出；
5. 正则筛选、全文抽取、繁简转换和可选 OpenAI 摘要/标题处理；
6. OpenAPI 元数据接口、健康检查和指标；
7. npm 包模式的 `init()`、`request()`、`registerRoute()`。

规模证据：`lib/routes` 静态包含 6,523 个文件，其中 1,970 个名为
`namespace.ts`，另有 4,550 个非 namespace 的 TS/TSX 文件。后者包含工具、模板和
测试，不能直接当作“路由数量”；未运行构建脚本，故不声称精确路由数。

## 与“即时 AI”的重合度

重合度：**中高，集中在采集层；不覆盖情报主链路的后半段。**

- 直接重合：财经媒体、交易所公告、公司信息、黄金行业、宏观研报等来源适配；统一
  Feed 模型；缓存；筛选；来源扩展。
- 部分重合：OpenAI 摘要/翻译是请求参数触发的轻量后处理，不是可审计的 AI 情报流水线。
- 不重合：没有正式新闻数据库、历史事件库、跨源去重、实体识别、重要度评分、专题
  档案、主动调度或主动推送。

财经源码证据：

| 来源能力 | 源码与标识符 | 结论 | 状态 |
|---|---|---|---|
| 巨潮公司公告 | `lib/routes/cninfo/announcement.ts`，`handler()`、`hisAnnouncement/query` | 按证券代码、组织 ID、公告分类和关键字抓取公告 | `SOURCE_VERIFIED` |
| 上交所披露 | `lib/routes/sse/disclosure.ts`，`handler()`、`queryCompanyBulletin.do` | 获取上市公司公告并保留 PDF 链接 | `SOURCE_VERIFIED` |
| 雪球股票信息 | `lib/routes/xueqiu/stock-info.ts`，`typeMap`、`handler()` | 获取公告、资讯与讨论；标记 `antiCrawler` | `SOURCE_VERIFIED` |
| 金十快讯 | `lib/routes/jin10/index.ts`，`cache.tryGet('jin10:index')` | 获取市场快讯，保留 `important` 标志与稳定 guid | `SOURCE_VERIFIED` |
| 财联社电报 | `lib/routes/cls/telegraph.tsx`，`handler()` | 获取电报并映射分类、时间、正文 | `SOURCE_VERIFIED` |
| 东方财富研报 | `lib/routes/eastmoney/report/index.tsx`，`handler()` | 抓取宏观、行业、个股等研报及详情 | `SOURCE_VERIFIED` |
| 中国黄金协会 | `lib/routes/cngold/index.ts`，`handler()` | 抓取黄金行业、矿业、市场、政策等栏目 | `SOURCE_VERIFIED` |

## 最强的五项能力

1. **来源覆盖与社区化扩展规模大**：Route/Namespace 模式把每个来源隔离为小模块。
2. **真实的请求到 Feed 全链路完整**：注册、抓取、规范化、缓存、参数处理、渲染均有核心实现。
3. **数据源工程工具丰富**：统一 `ofetch/got`、代理、浏览器自动化、日期解析、内容缓存。
4. **部署形态多**：Node、Docker、Cloudflare Worker/Container、Vercel 兼容入口和 npm 包模式。
5. **对“即时 AI”有直接价值的财经路由已存在**：公告、快讯、研报、黄金行业等均有源码实现。

## 最大的五项问题

1. **不是情报数据库**：Feed 按请求生成，缓存不是历史归档；不能承担证据库和长期研究库。
2. **没有调度/主动推送**：未发现 cron、scheduler 或 webhook 主链路；需要外部轮询与编排。
3. **跨源去重与实体/事件建模缺失**：`guid` 仅支持 Feed 条目标识，核心没有历史去重表。
4. **来源稳定性与合规成本高**：大量第三方 API、HTML、Cookie、反爬与浏览器依赖会持续变化。
5. **AGPL-3.0 网络条款影响集成边界**：修改后通过网络服务提供交互时需提供对应源码；不宜
   未经法律与架构评审直接嵌入闭源桌面核心。

## 静态结论

推荐角色：`SIDE_CAR_SERVICE`。优先把原版或合规维护的 RSSHub 作为本机独立进程，通过
HTTP Feed/API 接入“即时 AI”；不建议把整个仓库 Fork 后混入正式客户端。最终采用决定
仍需运行验证、来源验收、许可证矩阵与用户审批。
