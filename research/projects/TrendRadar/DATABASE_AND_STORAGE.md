# TrendRadar 数据库与存储

项目/提交：TrendRadar / `8ee26026ba6c11dec41a95fb3895a7162876caa1`；来源：`OFFICIAL_ARCHIVE_SNAPSHOT`；本报告为静态分析。

## 后端选择

`trendradar/storage/manager.py::StorageManager` 把 `auto/local/remote` 映射到统一 `StorageBackend`：

- 本地：`LocalStorageBackend`，SQLite 为主，TXT/HTML 可选。
- 远程：`RemoteStorageBackend`，在临时目录操作 SQLite，再把整个日库上传到 S3 兼容对象键 `news/{date}.db` 或 `rss/{date}.db`。
- `auto`：GitHub Actions 且远程配置完整时选 remote，否则 local。

状态：`SOURCE_VERIFIED`。

## 文件布局

```text
output/
  news/YYYY-MM-DD.db
  rss/YYYY-MM-DD.db
  txt/YYYY-MM-DD/HH-MM.txt        # 可选
  html/YYYY-MM-DD/HH-MM.html
  html/latest/{mode}.html
  index.html
```

根目录还会由报告生成器复制一份 `index.html`。快照自身包含 `output/news/2025-12-21.db` 至 `2025-12-27.db` 七个归档数据库；这些是上游快照数据，不应复制进正式产品或 Git。

## 数据模型

### 内存模型

- `storage/base.py::NewsItem`：title/source/rank/url/mobile_url/crawl_time/ranks/first_time/last_time/count/rank_timeline。
- `NewsData`：date/crawl_time/按 source 分组 items/id_to_name/failed_ids。
- `RSSItem`：title/feed/url/guid/published_at/summary/author 与抓取统计。
- `RSSData`：date/crawl_time/按 feed 分组 items/id_to_name/failed_ids。

### 新闻日库

`storage/schema.sql` 定义：

- `platforms`
- `news_items`
- `title_changes`
- `rank_history`
- `crawl_records`
- `crawl_source_status`
- `period_executions`

新闻保留原始链接字段 `url/mobile_url`，但主要内容是标题，不保存网页原文。`rank_history` 以 rank=0 表示脱榜。

### RSS 日库

`storage/rss_schema.sql` 定义 `rss_feeds`、`rss_items`、`rss_crawl_records`、`rss_crawl_status`，另保留旧式 `rss_push_records`。RSS 保存 URL、GUID、发布时间、最长 500 字的清洗摘要和作者，不是原文证据副本。

### AI 筛选表

`storage/ai_filter_schema.sql` 在新闻日库增加：

- `ai_filter_tags`：兴趣文件、prompt hash、版本、priority/status。
- `ai_filter_results`：新闻 × 标签、relevance score、active/deprecated。
- `ai_filter_analyzed_news`：记录已送 AI 的 ID，避免重复 token 消耗。

## 去重与历史能力

| 场景 | 实现 | 边界 |
|---|---|---|
| 热榜持久化去重 | `_save_news_data_impl`：规范化 URL + platform | URL 为空时直接插入，不去重 |
| RSS 持久化去重 | GUID + feed 优先，URL + feed 回退 | 两者都空时不保存 |
| 热榜“新增” | `_detect_new_titles_impl`：比较同平台历史标题 | 标题变更/同标题异 URL 的语义边界有限 |
| RSS“新增” | `_detect_new_rss_items_impl`：比较 URL | 与持久化 GUID 优先策略不完全一致 |
| 标题演化 | `title_changes` | 仅同 URL 记录 |
| 排名演化 | `rank_history` | 按日库隔离，跨日需应用层汇总 |

全部为 `SOURCE_VERIFIED`。

## 历史查询与迁移评价

- MCP 的 `ParserService`/`DataService` 能遍历可用日期并查询日库，支持历史搜索和时期对比；不是统一 SQL 数据仓库。
- SQLite 易于在 Windows 搬运和备份，但按日双库不利于跨日实体分析、全文搜索、事务一致性和长期 schema migration。
- 远程后端传输整个 SQLite 对象；并发写入与对象级覆盖语义需要运行/多实例验证。
- 对“即时 AI”建议：可将此 schema 作为实验原型；正式长期库应增加 source/document/event/entity/evidence 模型、内容哈希、抓取快照和跨日索引，业务文件实际落到 `H:\即时AI文件库`。

## 一个源码一致性风险

`RSSItem.to_dict/from_dict` 未包含 `guid`，而 SQLite 保存逻辑依赖 `getattr(item, 'guid')`。当前主抓取→SQLite 路径保留 guid，但任何经过 `to_dict/from_dict` 的序列化往返都会丢失 guid。验证：`storage/base.py:89-120` 与 `sqlite_mixin.py:852-868`，`SOURCE_VERIFIED`。

