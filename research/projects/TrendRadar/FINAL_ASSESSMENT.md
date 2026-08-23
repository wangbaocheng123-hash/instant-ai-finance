# TrendRadar 最终静态评估

## 结论先行

TrendRadar 是本轮最接近“信息采集→降噪→AI→报告→推送”主链的候选，建议角色为 `CORE_FORK_CANDIDATE`，静态评分 **76/100**。它尚不能被定为 `PRIMARY_BASE`：当前只有无 `.git` 的官方归档快照、没有运行证据，且 GPL-3.0、默认公共 NewsNow 依赖、采集调度缺陷和证据库模型不足都需要过闸门。

项目/提交：`sansan0/TrendRadar` / `8ee26026ba6c11dec41a95fb3895a7162876caa1`；版本 6.10.0；来源 `OFFICIAL_ARCHIVE_SNAPSHOT`。

## 统一评分

| 评价项目 | 得分 | 理由与证据 |
|---|---:|---|
| 与财经情报需求匹配度 | 17/20 | `NewsAnalyzer.run` 已贯通采集、筛选、AI、报告、推送；缺官方公告、财报/行情实体模型与原文证据归档 |
| 已有功能完整度 | 13/15 | 热榜+RSS、三模式、日库历史、HTML、九渠道、MCP、S3；无桌面壳/全文证据库 |
| 代码可维护性 | 8/10 | 分包和 `AppContext/StorageBackend` 边界较清楚；但 `__main__.py`/MCP analytics 过大，快照无测试 |
| 扩展和适配能力 | 8/10 | RSS、API URL、LiteLLM、StorageBackend、generic webhook、MCP；无动态插件注册 |
| Windows 本地运行能力 | 7/10 | Python 原生脚本与 bat；不需 WSL/Docker；但未运行，bat 版本要求文案冲突且会自动安装 uv |
| 数据来源能力 | 8/10 | 11 个默认热榜配置 + 任意 RSS/Atom/JSON Feed；热榜集中依赖第三方 NewsNow 风格 API |
| AI 与过滤能力 | 8/10 | 关键词 DSL、权重、AI 标签增量分类、AI 分析/翻译；需隐私、提示注入与评估闭环 |
| 上游活跃度 | 2/5 | 本快照无 `.git`，本子任务未联网，无法验证当前活跃度；仅能确认锁定版本 6.10.0 |
| 许可证适配性 | 2/5 | GPL-3.0 允许 Fork 但对嵌入/分发约束强；产品发布模型未决定 |
| 改造成本 | 3/5 | 可较快原型化，但需改证据模型、来源层、调度 bug、安全、H 盘路径和桌面集成 |
| **总分** | **76/100** | 静态候选，非最终底座决定 |

## 源码证据链

1. `trendradar/__main__.py::NewsAnalyzer.run/_execute_mode_strategy/_run_analysis_pipeline`：主业务贯通；`SOURCE_VERIFIED`。
2. `crawler/fetcher.py::DataFetcher`：默认 NewsNow API、域名校验和批量抓取；`SOURCE_VERIFIED`。
3. `storage/schema.sql`, `rss_schema.sql`, `sqlite_mixin.py`：URL/GUID 去重、标题/排名历史、按日库；`SOURCE_VERIFIED`。
4. `core/frequency.py`, `core/analyzer.py`：规则 DSL 和权重；`SOURCE_VERIFIED`。
5. `ai/filter_pipeline.py`, `ai/analyzer.py`, `ai/client.py`：兴趣标签分类与 LiteLLM 分析；`SOURCE_VERIFIED`。
6. `notification/dispatcher.py`：九类渠道分发；`SOURCE_VERIFIED`。
7. `mcp_server/server.py`：stdio/HTTP 与查询、抓取、同步、文章、通知 tools；`SOURCE_VERIFIED`。
8. 根 `LICENSE`：GPL Version 3；`SOURCE_VERIFIED`。

## 适合复用

- 作为可运行的整体研究样机或接受 GPL 的 Fork 核心。
- 作为独立 GPL sidecar/MCP 服务，向“即时 AI”暴露受控查询/抓取接口。
- 独立重写关键词 DSL、排名轨迹、兴趣标签版本化、调度和通知分片等模式。
- 直接评估 LiteLLM/feedparser 等上游库，而非复制 TrendRadar 薄封装。

## 不建议直接采用

- 不把公共 NewsNow API 当作唯一、可审计的财经证据源。
- 不把按日标题 SQLite 直接定为“即时 AI”最终长期数据库。
- 不把 GPL 模块复制进许可证未决的正式桌面客户端。
- 不在未鉴权情况下启动 `0.0.0.0` MCP HTTP。
- 不依赖当前 `schedule.collect` 实现做隐私/成本控制。
- 不用 Jina Reader 绕过付费墙或登录墙。

## 进入下一闸门前必须完成

1. 用真正的浅克隆替代/补充快照，保留 `.git`、remote、提交和许可证历史；当前不算完成克隆。
2. Windows Python 3.12 受控运行：先 doctor，再单源抓取、二次去重、SQLite/HTML、MCP stdio。
3. 动态确认依赖体积、端口、停止清理和代理行为。
4. 修复/验证 collect 调度时序与 MCP HTTP 访问控制。
5. 完成 GPL 集成方案的法律/发布模型确认。
6. 设计把业务数据迁到 `H:\即时AI文件库` 的实验映射，禁止写入 Git。

## 推荐架构角色

当前：`CORE_FORK_CANDIDATE`。

条件分支：

- 若接受 GPL-3.0 整体发布并且 Windows 验证通过：继续评估 `PRIMARY_BASE`。
- 若桌面核心要保持不同许可证：改为 `SIDE_CAR_SERVICE` 或 `PATTERN_REFERENCE`。
- 若可靠官方财经源和长期证据库优先级最高：TrendRadar 只承担热点降噪/通知，不承担事实主库。

