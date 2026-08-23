# 金融数据模式

解决问题：以统一、可追溯接口取得行情、财报、监管披露、宏观、利率和商品数据，并与新闻/事件证据关联。

OpenBB 的 Provider registry、Fetcher TET、180 个标准模型和 Python/REST/MCP 表面是六仓最佳实现；RSSHub 有 CNInfo、SSE、雪球、金十、财联社、东方财富报告和中国黄金协会等路由，适合作为信息 Feed 补充；TrendRadar 只有财经热点线索；n8n 可调用 API 但无金融 schema；Folo/changedetection 不提供结构化金融层。

推荐 OpenBB 作为 `DATA_PROVIDER`，通过最小 localhost sidecar 或受控库依赖接入。即时 AI 在边界处映射为自有 schema，并记录 provider、route、query、时间、原始 URL、币种/单位和数据条款。OpenBB AGPL 和每个数据 Provider 条款必须分别审查。

建议接口：`query(dataset, symbols, start, end, provider) -> FinancialObservation[]`；不能把任何返回直接解释为交易信号。
