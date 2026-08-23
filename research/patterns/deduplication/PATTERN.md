# 去重模式

解决问题：同一事实可能由同源重复抓取、不同 URL、不同媒体转载或标题改写产生；系统既要降噪，又不能丢失独立来源证据。

TrendRadar 使用 platform+规范 URL、RSS GUID/URL、标题变化和排名历史；changedetection 用同 watch 原始 checksum、过滤后 MD5 和历史唯一行；n8n `RemoveDuplicatesV2` 保存批内/跨次 hash 或 latest 值；Folo 只按远端 entry ID upsert；RSSHub 缓存不是长期新闻去重；OpenBB 未定位新闻去重。

最佳单仓模式是 TrendRadar 的 URL/GUID/历史组合，但它不足以跨源语义聚类。推荐 `REWRITE_FROM_PATTERN`：分四层执行外部 ID、规范 URL、内容 hash、标题/实体/事件相似簇；所有重复项合并到 canonical item，同时分别保留 evidence。

限制：规范 URL 规则会误合并，语义聚类会产生不可解释错误；必须保存规则版本、cluster reason 和人工拆分能力。TrendRadar GPL、changedetection Apache/商业文档冲突、n8n SUL 均不适合直接复制实现。

建议接口：`deduplicate(EvidenceEnvelope) -> {canonical_id, decision, matched_by, score, rule_version}`。
