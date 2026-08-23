# RSSHub 数据库与存储

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`）。

## 结论

RSSHub 核心没有新闻数据库、ORM、迁移文件或历史事件模型。它是即时生成 Feed 的服务，
持久化能力仅限可过期缓存、运行日志以及 Docker 组合中的 Redis volume。不能把这些替代
“即时 AI”的 `H:\即时AI文件库` 业务数据库和证据库。

## 数据模型

`lib/types.ts` 定义内存中的 Feed 合同：

- `Data`：`title/link/description/item[]/lastBuildDate/ttl/...`
- `DataItem`：`title/description/pubDate/link/category/author/guid/content/...`
- `Route`：path、metadata、feature flags、radar、handler

这些是 TypeScript 类型，不是数据库 schema。没有发现表、索引、迁移或持久化 repository。
验证状态：`SOURCE_VERIFIED`。

## 缓存后端

| 后端 | 源码/配置 | 保存内容 | 持久性 |
|---|---|---|---|
| Memory LRU | `lib/utils/cache/memory.ts`，`MEMORY_MAX` | 路由结果和详情缓存 | 进程内，退出即失 |
| Redis | `lib/utils/cache/redis.ts`，`REDIS_URL` | 字符串化对象与 TTL sidecar | 取决于 Redis；RSSHub 不管理历史 schema |
| HTTP cache | `lib/utils/cache/http.ts`，`CACHE_HTTP_URL/TOKEN` | 远端 key/value + TTL | 外部服务负责 |
| Worker KV | `lib/utils/cache/kv.ts` | key/value + TTL | Cloudflare KV，可过期 |
| Route cache | `lib/middleware/cache.ts` | 按 path/format/limit 哈希的整条 `Data` | TTL，默认 5 分钟 |
| Content cache | `cache.tryGet()` | 文章详情、全文、OpenAI 结果等 | TTL，默认 1 小时 |

`docker-compose.yml` 的 `redis-data:/data` 只是 Redis 数据卷；它不改变缓存语义，也没有
正式新闻记录的数据治理。

## 日志

`lib/utils/logger.ts` 默认在非 Vercel 且 `NO_LOGFILES=false` 时写：

- `logs/error.log`
- `logs/combined.log`

这会写到进程工作目录。若未来受控运行，必须在 experiment 环境把日志路径/工作目录映射
到临时目录或 H 盘规定的 `logs/`，不得污染研究仓库；本子任务未运行。

## 新闻保存、原始链接与历史查询

- 新闻保存：不保存为长期记录，只在 Feed 对象或缓存中短暂存在。
- 原始链接：`DataItem.link` 普遍保留；是否指向正文、API 或 PDF 取决于 Route。核心只定义
  字段，不保证每条 Route 的链接语义。
- 原文证据：没有统一保存 HTML/PDF 原文、响应头、抓取时间和内容哈希。
- 历史查询：无数据库查询接口；只能依赖上游当前页面、缓存或下游 Feed 阅读器。

## 去重

`lib/views/rss.tsx` 用 `guid || link || title` 生成 RSS guid；`app.test.ts::checkRSS` 检查
单次测试 Feed 中 guid 不重复。这不是跨来源、跨时间去重。没有内容哈希、canonical URL 表、
实体事件键或合并审计。

## 迁移与 Windows 适配

- RSSHub 自身无业务数据库迁移问题；独立服务可视为无状态，Redis 可选。
- 对 Windows 最轻的静态方案是 Node + memory cache；可靠性和资源占用尚未运行验证。
- 若使用 Docker Compose，则需要 Docker Desktop（当前未安装且未经批准），并会增加 Redis 与
  browserless 依赖。
- 正式集成必须由“即时 AI”下游把 Feed 标准化为自己的记录，并将原文、数据库、日志、缓存
  按项目章程写入 `H:\即时AI文件库` 对应目录。

## 对即时 AI 的建议接口

```text
RSSHub localhost Feed
-> ingestion adapter
-> source_id + route + canonical_url + guid + fetched_at
-> raw/evidence 原文留存
-> database 规范化记录与去重索引
-> 后续实体、事件、评分、AI 与通知
```

不要把 Redis cache 当作唯一证据来源；缓存清除不应导致业务历史丢失。
