# 来源索引

| 项目/提交 | 源码 | 能力 |
|---|---|---|
| TrendRadar `8ee2602` | `storage/sqlite_mixin.py`; `schema.sql`; `rss_schema.sql` | URL/GUID、标题变化、排名历史 |
| changedetection `fce2478` | `processors/text_json_diff/processor.py`; `model/Watch.py` | checksum/MD5/unique lines |
| n8n `7968432` | `RemoveDuplicatesV2.node.ts`; `DataDeduplicationService`; `DeduplicationHelper` | workflow/node 作用域跨次去重 |
| Folo `7c220c6` | `database/src/services/entry.ts`; schemas | ID upsert，无 GUID unique/content hash |
| RSSHub `5151c32` | cache middleware | 响应缓存，不是历史业务去重 |
| OpenBB `3e071fc` | `standard_models/*news*` | 新闻有 URL，未见本地去重实现 |
