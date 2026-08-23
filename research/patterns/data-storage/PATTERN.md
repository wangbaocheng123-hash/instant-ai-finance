# 数据存储模式

解决问题：在 Windows 单机长期保存原文证据、规范条目、实体/事件、规则和 AI 决策，同时支持全文搜索、备份和可迁移性。

TrendRadar 的每日 SQLite 适合原型和排名历史，但不利于跨日实体研究且不保存完整原文；changedetection 的每 watch 文件历史适合页面版本证据，但无查询模型；OpenBB 没有中心业务库；Folo WA-SQLite/IndexedDB 会裁剪客户端缓存；n8n SQLite/Postgres 保存 workflow execution 而非财经事实；RSSHub 只有缓存。

六仓都不能直接作为正式库。推荐自有模式：`raw/` 内容寻址文件 + `evidence/` 清单 + SQLite WAL/FTS5 候选元数据目录；数据库只引用不可变原文，并拥有 schema migration、backup manifest 和 integrity check。

建议实体：Source、FetchRun、Evidence、CanonicalItem、DuplicateLink、Entity、Event、RuleDecision、AIResult、NotificationOutbox。业务根固定为 `H:\即时AI文件库`；cache/logs 与正式库隔离。
