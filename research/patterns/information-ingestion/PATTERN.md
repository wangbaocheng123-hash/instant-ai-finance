# 信息采集模式

解决问题：用统一入口接收 Feed、官方网页变化、热点线索和结构化金融 API，同时保留来源、授权和原始证据。

实现对比：RSSHub 的 route handler 最擅长把大量网站转为 Feed；changedetection.io 的 ticker/worker/fetcher/processor 最擅长监测没有 Feed 的具体官方页面；OpenBB 的 Provider/Fetcher TET 最擅长结构化金融查询；TrendRadar 可批量采集热榜与 RSS；n8n 提供通用 HTTP/RSS 编排；Folo 只有客户端调用点，核心采集后端不在仓库。

最佳组合不是单一实现，而是“RSSHub + changedetection + OpenBB”独立服务，经即时 AI 的 Evidence Intake 统一接入。TrendRadar 作为热点补充，n8n 后置，Folo 不承担采集。

推荐方式：`SIDE_CAR_SERVICE` + `API_INTEGRATION` + 自有 `ADAPTER`。依赖和限制包括各服务运行时、来源条款、SSRF/速率/缓存控制；许可证涉及 RSSHub AGPL、changedetection 许可冲突待澄清、OpenBB AGPL、TrendRadar GPL。

建议接口：`fetch(source, cursor) -> EvidenceEnvelope[]`，每条至少含 `source_id/source_url/fetched_at/http_status/content_type/raw_hash/raw_locator/published_at/external_id/terms_tag`。不得让 sidecar 直接写正式数据库。
