# TrendRadar 模块地图

项目/提交：TrendRadar / `8ee26026ba6c11dec41a95fb3895a7162876caa1`；来源为 `OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`，不算完成克隆；许可证列均基于根 `LICENSE` 的 GPL-3.0，仍需法律复核。

| 模块 | 作用/入口 | 主要依赖 | 独立性 | 建议复用 | 难度 | 许可证影响 |
|---|---|---|---|---|---|---|
| `trendradar/__main__.py` | `main`, `NewsAnalyzer.run` 总编排 | 几乎所有内部层 | 低 | `DESIGN_REFERENCE` 或整体 Fork | 高 | 直接复制受 GPL |
| `trendradar/context.py` | `AppContext` 依赖汇聚 | core/report/notification/AI/storage | 中 | `DESIGN_REFERENCE` | 中 | GPL |
| `trendradar/crawler/fetcher.py` | NewsNow API 抓取与域名校验 | requests | 高 | `ADAPTER` 思路；不直接复制优先 | 低 | GPL；另有上游 API 信任问题 |
| `trendradar/crawler/rss/` | RSS/Atom/JSON Feed 解析抓取 | requests, feedparser | 高 | `REWRITE_FROM_PATTERN` | 低-中 | GPL；feedparser 自身许可另查 |
| `trendradar/core/frequency.py` | 丰富关键词 DSL | re/filesystem | 高 | `REWRITE_FROM_PATTERN` 或整体 GPL Fork 内复用 | 中 | GPL |
| `trendradar/core/analyzer.py` | 权重、关键词/RSS 统计、展示转换 | frequency | 中 | `REWRITE_FROM_PATTERN` | 中 | GPL |
| `trendradar/core/scheduler.py` | 时间线解析、once 记录 | storage 接口 | 高 | `REWRITE_FROM_PATTERN`；先修采集时序 | 中 | GPL |
| `trendradar/ai/client.py` | LiteLLM 适配 | litellm | 高 | `LIBRARY_DEPENDENCY` 直接依赖 LiteLLM，不复制薄封装 | 低 | TrendRadar 封装 GPL；LiteLLM 许可另查 |
| `trendradar/ai/analyzer.py` | 新闻分析提示与 JSON 结果 | AIClient/prompts | 中 | `DESIGN_REFERENCE` | 中 | GPL |
| `trendradar/ai/filter.py` + `filter_pipeline.py` | 兴趣→标签→增量批分类→入库 | AIClient/storage | 中 | `DESIGN_REFERENCE`/`REWRITE_FROM_PATTERN` | 高 | GPL |
| `trendradar/ai/translator.py` | 单条/批量翻译 | AIClient | 高 | `DESIGN_REFERENCE` | 低 | GPL |
| `trendradar/storage/base.py` | 数据类与 StorageBackend 抽象 | 标准库 | 中 | `DESIGN_REFERENCE` | 中 | GPL |
| `trendradar/storage/sqlite_mixin.py` | 去重、历史、AI 筛选 SQL | SQLite/schema | 低-中 | `DESIGN_REFERENCE` | 高 | GPL |
| `trendradar/storage/local.py` | 日库 + TXT/HTML | SQLite | 中 | 整体 Fork 内复用 | 中 | GPL |
| `trendradar/storage/remote.py` | S3 上下载日 SQLite | boto3 | 中 | `SIDE_CAR_SERVICE` 内使用 | 中 | GPL |
| `trendradar/report/` | 报告数据与静态 HTML | 内部模型 | 低 | `UI_REFERENCE`/`DESIGN_REFERENCE` | 中 | GPL |
| `trendradar/notification/` | 九渠道格式/分片/发送 | requests/smtplib | 中 | `SIDE_CAR_SERVICE` 或模式参考 | 中-高 | GPL |
| `trendradar/commands/` | doctor/status/test/version | 主配置 | 中 | `DESIGN_REFERENCE` | 低 | GPL |
| `mcp_server/server.py` | FastMCP resources/tools 注册 | fastmcp/tool classes | 低 | `SIDE_CAR_SERVICE` | 中 | GPL |
| `mcp_server/services/` | SQLite 解析、查询、缓存 | output DB | 中 | `SIDE_CAR_SERVICE` | 中 | GPL |
| `mcp_server/tools/` | 查询/分析/抓取/同步/文章/通知 | services + 外网 | 中 | 选择性服务化，不直接嵌入 | 高 | GPL |
| `docker/` | cron、静态 Web、镜像与 compose | Docker/supercronic | 中 | 部署参考 | 中 | GPL；基础镜像/二进制许可另查 |

## 模块边界判断

- 最清晰的可替换边界是 `StorageBackend`、`PLATFORMS_API_URL`、RSS feeds、LiteLLM model/api_base、Generic Webhook 和 MCP 进程边界。
- 没有动态插件注册框架。通知渠道、MCP 工具和核心报告区域都是代码内固定枚举/分支，新增一种通常需要改源码。
- `mcp_server/tools/analytics.py` 体量很大，且与主 `core/analyzer.py` 存在同名权重计算，后续维护应检查逻辑漂移。

验证状态：上述模块和依赖关系均 `SOURCE_VERIFIED`；可维护性判断是基于源码结构的工程推断。
