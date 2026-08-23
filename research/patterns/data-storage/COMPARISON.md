# 实现比较

| 模式 | 证据版本 | 查询能力 | 长期适配 |
|---|---:|---:|---:|
| TrendRadar 每日 SQLite | 中 | 中 | 中低 |
| changedetection 文件历史 | 高（单页面） | 低 | 中（sidecar 私有） |
| Folo 客户端缓存 | 低 | 中 | 低 |
| n8n execution DB | 运行审计 | 中 | 低（财经业务） |
| OpenBB/RSSHub cache | 低 | 低 | 低 |
| 自有 evidence + catalog | 高 | 高 | **推荐** |
