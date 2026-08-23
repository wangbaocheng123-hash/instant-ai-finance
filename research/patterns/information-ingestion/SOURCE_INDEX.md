# 来源索引

| 项目/提交 | 源码位置 | 关键实现 |
|---|---|---|
| TrendRadar `8ee2602` | `trendradar/crawler/fetcher.py`; `crawler/rss/*` | `DataFetcher`、`RSSFetcher/RSSParser` |
| RSSHub `5151c32` | `lib/registry.ts`; `lib/router.ts`; `lib/routes/**` | route registry/handler/context |
| changedetection `fce2478` | `changedetectionio/worker.py`; `content_fetchers/__init__.py`; `processors/**` | worker、fetcher 选择、processor |
| OpenBB `3e071fc` | `openbb_core/provider/abstract/fetcher.py`; `query_executor.py` | Provider/Fetcher TET |
| Folo `7c220c6` | `apps/desktop/src/lib/api-client.ts`; `env.common.ts` | 证明客户端依赖仓库外 API |
| n8n `7968432` | `RssFeedRead*.node.ts`; `HttpRequestV3.node.ts` | 通用 RSS/HTTP 节点 |
