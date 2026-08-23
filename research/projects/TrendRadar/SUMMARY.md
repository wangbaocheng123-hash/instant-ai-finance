# TrendRadar 摘要

## 研究边界与身份

- 项目：`sansan0/TrendRadar`
- 固定提交：`8ee26026ba6c11dec41a95fb3895a7162876caa1`
- 版本：`6.10.0`（`pyproject.toml` 与根 `version`）
- 标签：本地无 `.git`，无法确认对应 tag。
- 活跃度：本子任务未联网且快照无历史，无法确认当前上游活跃度。
- 主要语言：Python；最低版本以 `pyproject.toml` 的 `requires-python = ">=3.12"` 为准。
- 来源：`OFFICIAL_ARCHIVE_SNAPSHOT`。快照无 `.git`，不能算完成克隆；提交与默认分支 `master` 是本轮外部锁定元数据，无法由快照独立复核。
- 研究状态：静态核心调用链已阅读；未安装依赖、未启动、未联网抓取。

`DOC_ONLY`：无关键结论仅依赖 README；仅文档存在但未源码验证的宣传能力未计入评分。

## 它实际解决什么问题

TrendRadar 是一个定时热点/RSS 聚合、筛选、报告和通知系统，而不是完整的交易终端或原文档案系统。主链路由 `trendradar/__main__.py::NewsAnalyzer.run` 编排：通过 `DataFetcher` 调用 NewsNow 风格 API，另由 `RSSFetcher` 抓取 RSS/Atom/JSON Feed，写入按日 SQLite，执行关键词或 AI 筛选，生成静态 HTML，并向九类通知渠道发送。

目标用户是希望低运维地监控公开热点和订阅源、按兴趣降噪并接收推送的个人/小团队。该判断来自程序能力边界，不依赖 README 宣传。

证据：

- 项目/提交：TrendRadar / `8ee26026ba6c11dec41a95fb3895a7162876caa1`
- 源码：`trendradar/__main__.py:1609-1636`
- 类/函数：`NewsAnalyzer.run`
- 调用关系：`_crawl_data` → `_crawl_rss_data` → `_execute_mode_strategy` → `_run_analysis_pipeline` → `_send_notification_if_needed`
- 结论：聚合、筛选、存储、报告、推送是一条实际可定位的主链。
- 验证状态：`SOURCE_VERIFIED`

## 与“即时 AI”财经情报需求的重合

重合度较高但不完整，静态估计约 70%：它已有多源入口、去重、历史排名、新增识别、关键词/AI 筛选、AI 摘要、证据链接、HTML、通知、MCP 和本地 SQLite。缺口是官方公告专用适配器、财报/行情结构化模型、公司/商品实体主数据、可信来源等级、原文证据归档、全文检索、审计式证据链和 Windows 桌面壳。

默认热榜并非逐站直采：`trendradar/crawler/fetcher.py::DataFetcher.DEFAULT_API_URL` 指向 `https://newsnow.busiyi.world/api/s`，可由 `platforms.api_url`/`PLATFORMS_API_URL` 替换。因而它更像“情报处理与推送核心”，不应被误认为已有可靠的官方财经采集层。

## 最强的五项能力

1. **端到端信息处理链**：抓取、SQLite、筛选、报告、通知在单一编排器内贯通。`SOURCE_VERIFIED`
2. **本地历史与去重**：新闻按规范化 URL + 平台去重，RSS 按 GUID 优先、URL 次之；保留标题变化、排名历史和抓取状态。`SOURCE_VERIFIED`
3. **可配置筛选**：关键词语法支持必须词、排除词、全局排除、正则、别名和每组上限；也能切换到 AI 标签分类。`SOURCE_VERIFIED`
4. **输出/接入面丰富**：静态 HTML、TXT、九类通知、MCP 查询/分析/触发抓取、S3 兼容远程 SQLite。`SOURCE_VERIFIED`
5. **调度表达力**：时间段、工作日计划、一次性分析/推送和模式覆盖均有代码实现。`SOURCE_VERIFIED`，但采集开关时序有缺陷，见风险报告。

## 最大的五项问题

1. **热榜上游集中依赖**：默认 NewsNow 公共 API 是单点和信任边界，站点真实性、可用性与授权需要单独治理。
2. **采集调度时序错误**：`NewsAnalyzer.run` 先抓取/保存，再由 `_execute_mode_strategy` 解析 `schedule.collect`；`collect=false` 只跳过分析，不会阻止采集。
3. **许可证约束强**：根许可证为 GPL-3.0；直接嵌入/修改后分发桌面产品可能触发整体 GPL 开源义务，不能在法律确认前混入正式产品。
4. **工程验证不足**：快照没有测试目录或 pyproject 测试依赖，且本任务未运行；Windows 脚本宣称 Python 3.10+，与 pyproject 要求 3.12+ 冲突。
5. **并非长期证据库**：按日拆库、标题级采集为主，RSS 摘要最多 500 字；没有原文快照、内容哈希、来源版本、实体关系或跨日统一检索索引。

## 初步角色

推荐角色：`CORE_FORK_CANDIDATE`，但带两个闸门：先完成 Windows 运行验证；再决定是否接受 GPL-3.0 的发布模型。若“即时 AI”不准备整体采用 GPL，优先把 TrendRadar 保持为独立进程/服务或仅复用设计模式，不能直接复制核心源码。
