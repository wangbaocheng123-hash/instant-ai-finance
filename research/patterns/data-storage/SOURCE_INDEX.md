# 来源索引

| 项目/提交 | 源码 | 借鉴/拒绝 |
|---|---|---|
| TrendRadar `8ee2602` | `storage/schema.sql`; `rss_schema.sql`; `sqlite_mixin.py` | 借鉴排名/标题历史；拒绝每日库作主库 |
| changedetection `fce2478` | `store/file_saving_datastore.py`; `model/Watch.py` | 借鉴原子写和内容版本；拒绝作查询库 |
| OpenBB `3e071fc` | `Preferences`; `OBBject`; Provider caches | 借鉴导出边界；结果需自有持久化 |
| Folo `7c220c6` | `database/src/db.desktop.ts`; schemas/services | 借鉴 cache hydration；拒绝当证据库 |
| n8n `7968432` | DB entities；ExecutionData；StorageConfig | 借鉴运行审计；不当业务库 |
| RSSHub `5151c32` | cache layer | 仅可删除缓存 |
