# RSSHub 真实数据流

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`）。

## 主调用链

```text
HTTP GET /<namespace>/<route>
-> lib/index.ts: serve(app.fetch)
-> lib/app.ts: 先安装 request-rewriter
-> lib/app-bootstrap.tsx: Hono 中间件
-> lib/registry.ts: 选择生产构建 registry 或开发惰性 registry
-> lib/registry-helpers.ts: wrappedHandler
-> lib/routes/<namespace>/<route>.ts[x]: handler(ctx)
-> ofetch/got/Playwright/SDK 请求外部来源
-> handler 返回 Data { title, link, item[] }
-> ctx.set('data', response)
-> middleware/cache.ts: 路由缓存和并发 claim
-> middleware/parameter.ts: 规范化、排序、筛选、限量、全文/AI 可选处理
-> middleware/anti-hotlink.ts + header.ts
-> middleware/template.tsx: RSS/Atom/JSON Feed/RSS3
-> HTTP Response
```

Hono 中间件采用洋葱式 `await next()`。入站先经过访问控制和缓存，再进入 Route；出站时
缓存先保存 Route 原始 `Data`，随后 parameter 等中间件进行请求特定处理，最后 template
渲染。这意味着通用 `filter`、`limit`、`chatgpt` 等后处理不必为每种参数保存独立路由
内容缓存；整条路由缓存 key 只显式包含 path、format、limit，具体语义仍需运行测试确认。

## 路由发现与加载流

```text
lib/routes/**/namespace.ts + route modules
-> registry-dev.createDevRegistry()（开发期惰性加载）
-> applyModulesToNamespaces()
-> scripts/workflow/build-routes.ts（构建期）
-> assets/build/routes.js + routes.json + radar-rules + route-paths
-> registry.ts（生产加载）
-> registerRssRoutes()/registerApiRoutes()
```

证据：`lib/registry.ts` 的 `NODE_ENV` 分支、`registry-helpers.ts` 的
`applyModulesToNamespaces/registerRssRoutes`、`scripts/workflow/build-routes.ts` 的生成逻辑。
验证状态：`SOURCE_VERIFIED`。

## 抓取与规范化

Route handler 通常执行三步：

1. 从 path/query 读取来源参数；
2. 用 `ofetch`、兼容 `got` 或 Playwright 请求来源；
3. 映射为 `DataItem` 的 `title/link/pubDate/description/category/author/guid`。

代表性真实链路：

| 路由 | 抓取 | 映射/缓存 | 证据 |
|---|---|---|---|
| `/cninfo/announcement/...` | POST 巨潮 `hisAnnouncement/query` | 公告标题、详情链接、公告时间 | `lib/routes/cninfo/announcement.ts::handler` |
| `/sse/disclosure/...` | GET 上交所 `queryCompanyBulletin.do` | PDF 链接、证券名、日期 | `lib/routes/sse/disclosure.ts::handler` |
| `/xueqiu/stock_info/...` | 先取 Cookie/quote，再调雪球 search/timeline API | 资讯/公告/讨论 | `lib/routes/xueqiu/stock-info.ts::handler` |
| `/jin10/:important?` | 金十 flash API | `cache.tryGet`、稳定 guid、important 过滤 | `lib/routes/jin10/index.ts::handler` |
| `/eastmoney/report/:category` | 列表 HTML 中提取 initdata，再逐项抓详情 | 详情使用 `cache.tryGet(item.link)` | `lib/routes/eastmoney/report/index.tsx::handler` |
| `/cngold/:category?` | 抓栏目 HTML，再并发抓正文 | 正文使用 `cache.tryGet(item.link)` | `lib/routes/cngold/index.ts::handler` |

## 通用后处理

`lib/middleware/parameter.ts::middleware` 在 Route 返回后处理：

- 解码实体、日期转 UTC、相对链接绝对化、移除 `<script>`、处理懒加载图片；
- 按 `pubDate` 排序；
- `filter*`/`filterout*`/`filter_time` 正则过滤；默认 RE2JS；
- `limit` 截断；
- `mode=fulltext` 使用 Mercury Parser 抽正文并缓存；
- `chatgpt` + `OPENAI_API_KEY` 调 Chat Completions 处理标题/描述并缓存；
- `opencc`、`brief`、Telegram Instant View 和 Sci-Hub 链接改写。

OpenAI 处理是 Feed 请求的可选后处理，没有任务队列、提示词版本表、审计记录或结构化
事件输出，因此不能等同于“即时 AI”的 AI 分析层。

## 缓存与并发流

`lib/middleware/cache.ts` 以 path + format + limit 的 XXH64 结果形成路由 key；另设
control key。`globalCache.claim()` 防止多个请求同时抓同一路由：Redis 使用 Lua 原子逻辑，
内存实现同步 claim；HTTP 和 Worker KV 是 best-effort。

`lib/utils/cache/index.ts::tryGet()` 供 Route 缓存文章详情或昂贵请求。后端可选：

- 进程内 LRU memory（默认）；
- Redis；
- HTTP cache；
- Worker KV；
- 无缓存 fallback。

这些都是可过期缓存，不是业务历史库。

## 条目标识、去重与历史

`DataItem.guid` 是可选字段；RSS renderer 在
`lib/views/rss.tsx` 中采用 `item.guid || item.link || item.title`，Atom/JSON 也有类似 fallback。
`lib/app.test.ts::checkRSS` 在测试中检查单个 Feed 响应里的 guid 唯一性。核心源码没有跨次、
跨源的已见条目表或内容指纹库，所以：

- 可借助稳定 guid 让下游阅读器识别条目；
- 不能宣称 RSSHub 已实现“即时 AI”所需的跨源和历史去重；
- 证据原文、首次发现时间、修订历史必须由下游数据库保存。

## 缺失的目标链路

```text
RSSHub 已有：来源 -> 抓取 -> Feed 标准化 -> 缓存 -> 可选轻量过滤/AI -> Feed 输出
即时 AI 仍需：调度 -> 持久化 -> 跨源去重 -> 实体识别 -> 事件分类
             -> 重要度评分 -> 证据归档 -> 专题查询 -> 低噪声通知
```

未发现源码支持时均视为 `UNVERIFIED/ABSENT_IN_CORE_SNAPSHOT`，不是对所有外围生态项目的
结论。
