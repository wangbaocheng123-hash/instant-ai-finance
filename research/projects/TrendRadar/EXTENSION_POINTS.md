# TrendRadar 扩展点

项目/提交：TrendRadar / `8ee26026ba6c11dec41a95fb3895a7162876caa1`；来源为 `OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`，不算完成克隆；静态验证。

## 已有扩展边界

| 扩展点 | 源码/配置 | 能力 | 评价 |
|---|---|---|---|
| 热榜 API 替换 | `DataFetcher(api_url)`；`platforms.api_url` / `PLATFORMS_API_URL` | 指向自部署 NewsNow 兼容 API | `ADAPTER` 边界清楚，但协议未抽象为接口 |
| 热榜平台配置 | `platforms.sources` | 新增 NewsNow 平台 ID、别名、expected_domain | 配置式 |
| RSS/Atom/JSON Feed | `rss.feeds`; `RSSParser` | 任意标准 feed | 最实用的数据源扩展点 |
| 关键词 DSL | `frequency_words*.txt`; `core/frequency.py` | 规则、正则、排除、别名、上限 | 无需改代码 |
| 时间线 | `timeline.yaml`; `Scheduler` | preset/custom 周计划、时段动作 | 无需改代码；collect 语义有 bug |
| AI Provider | `AIClient` + LiteLLM model/api_base/fallbacks | 多模型/兼容端点 | 通过第三方库扩展 |
| 存储后端 | `StorageBackend`, `StorageManager` | local/remote；可实现新 backend | 有抽象，但 manager 仍需改代码注册 |
| 通知 | `NotificationDispatcher` | 九固定渠道 + generic webhook | generic webhook 配置式；新增原生渠道需改分支 |
| MCP | `mcp_server/server.py @mcp.tool/resource` | 查询、分析、抓取、同步、通知 | 可新增 tool，但无插件发现机制 |
| S3 兼容存储 | `RemoteStorageBackend` | R2/OSS/COS/S3/MinIO 类端点 | API 配置式 |
| 报告区域 | `display.region_order/regions/standalone` | 热榜、RSS、新增、独立区、AI 区 | 区域集合代码固定 |
| CLI | `argparse` | doctor/status/test | 新命令需修改 `main` |

## 不存在或不足的机制

- 未发现 Python entry points、动态模块扫描或正式插件 manifest：无通用插件系统。
- 无 Provider/Adapter 注册表；热榜、通知、MCP tools 多为手工 if/注册。
- 无常规 REST/GraphQL API；HTTP 服务是 MCP `/mcp` 和静态文件服务器。
- 无 webhook 入站接收器；只有向外发送。
- 无数据库 migration 框架，主要依靠 `CREATE IF NOT EXISTS` 和局部 `_migrate_rss_schema`。
- 无自定义工作流节点系统。

## 对“即时 AI”的建议接口

```text
SourceAdapter.fetch(cursor) -> RawDocument[]
Normalizer.normalize(raw) -> EvidenceDocument
Deduplicator.upsert(document) -> DocumentId
FilterPolicy.evaluate(document, user_profile) -> Decision
Analyzer.analyze(document/event) -> AnalysisArtifact
Notifier.send(channel, artifact) -> DeliveryReceipt
```

TrendRadar 可通过一个独立 sidecar/MCP 适配器接入上述接口。若直接 Fork，应优先把 `DataFetcher`、存储、过滤和通知改成注册表式端口，同时把采集调度判断移到实际网络请求之前。

验证状态：机制存在与否均通过当前快照源码检索和入口调用链确认，`SOURCE_VERIFIED`；建议接口属于本研究建议。
