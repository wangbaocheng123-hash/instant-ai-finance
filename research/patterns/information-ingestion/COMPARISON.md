# 实现比较

| 实现 | 优势 | 缺口 | 选择 |
|---|---|---|---|
| RSSHub route | 来源转换面最广 | 无历史/业务调度，路由易随网站变化 | Feed sidecar |
| changedetection watch | 变化证据和版本强 | 非新闻聚合、文件 datastore | 官方页面 sidecar |
| OpenBB Provider | 金融 schema 和 Provider 边界强 | 无持续采集/证据库 | 数据 Provider |
| TrendRadar fetch | 主链完整、原型快 | 公共 NewsNow 依赖和证据不足 | 条件热点补充 |
| n8n nodes | 灵活 | 治理弱、平台重 | 后置编排 |
| Folo client | UI 工作流成熟 | 服务端采集不可审计 | 不采用 |
