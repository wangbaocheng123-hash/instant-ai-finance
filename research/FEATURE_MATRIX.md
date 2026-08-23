# 六仓功能矩阵

状态：`R0_STATIC_COMPLETE / RUNTIME_PARTIAL`。基线日期：2026-08-23。`●` 表示锁定提交源码中存在完整或主要实现；`◐` 表示部分能力、依赖仓库外服务或需要使用者自行编排；`○` 表示核心源码中未定位到该能力。它们不是运行成功标记。

| 能力 | TrendRadar | RSSHub | changedetection.io | OpenBB | Folo | n8n |
|---|---|---|---|---|---|---|
| 信息抓取 | ● 热榜 API + Feed | ● 请求驱动的路由转换 | ● requests/浏览器抓取 | ● Provider 按需查询 | ◐ 核心抓取在远端 API | ● HTTP/RSS 等通用节点 |
| RSS/Atom | ● 读取并标准化 | ● 核心输出能力 | ◐ 输出变化 Feed | ○ 非核心 | ◐ 客户端订阅，解析后端缺失 | ● 读取与轮询节点 |
| 网页变化 | ○ | ○ | ● diff/history/watch | ○ | ○ | ◐ 可自行编排，不是内建领域引擎 |
| 调度 | ● timeline/period/once | ○ 服务本身按请求运行 | ● ticker/queue/worker | ○ | ◐ 远端任务，执行端不可见 | ● trigger/cron/durable scheduler |
| 去重 | ● URL/GUID/标题与排名历史 | ◐ 缓存，不是新闻历史去重 | ● 同一 watch 的 checksum/MD5/unique lines | ○ | ◐ 本地仅按远端 ID upsert | ● 批内与 workflow/node 跨次去重 |
| 分类/筛选 | ● 关键词 DSL、权重、AI 标签 | ○ | ◐ selector/trigger/conditions | ◐ 标准金融模型，不是事件分类 | ◐ Action/AI 界面，服务端不可见 | ◐ IF/Switch/Code/AI 由流程作者定义 |
| 搜索 | ● MCP 历史搜索 | ○ | ◐ watch/history/API 查询 | ◐ 远端结构化查询 | ● 本地 Fuse + 远端搜索 | ◐ workflow/execution 查询 |
| 持久化 | ● 每日 SQLite + HTML/TXT | ◐ 响应缓存 | ● JSON + 历史快照文件 | ◐ 设置/缓存/导出，无中心业务库 | ◐ WA-SQLite/IndexedDB 客户端缓存 | ● SQLite/Postgres + execution/binary |
| AI 摘要 | ● LiteLLM | ○ | ● 变化后可选 LiteLLM 摘要 | ○ `to_llm` 仅序列化 | ◐ 远端 API 执行 | ● 多模型/Agent 节点 |
| AI 筛选 | ● 兴趣标签增量流水线 | ○ | ◐ LLM change evaluator | ○ | ◐ 客户端调用点，后端缺失 | ◐ 可编排，缺财经默认策略 |
| 通知 | ● 九类渠道与分片 | ○ | ● Apprise | ○ | ◐ 桌面/推送依赖远端 | ● 多通知节点 |
| Webhook | ● Generic webhook 出站 | ○ | ◐ Apprise/HTTP 出站 | ○ | ◐ integrations/客户端调用 | ● 入站与出站 |
| API | ◐ MCP 为主，无常规业务 REST | ● HTTP 路由服务 | ● REST v1 | ● FastAPI/Platform API | ● 客户端 SDK 调远端 API | ● REST/Public API/Webhook |
| MCP | ● 查询、分析、抓取、通知工具 | ○ | ○ | ● OpenAPI 路由映射 | ◐ 客户端/远端调用 | ● client/tool/server trigger |
| 行情/财报 | ○ | ◐ 少量财经路由，非结构化行情平台 | ○ | ● 多 Provider/标准模型 | ○ | ◐ 需外接 API 节点 |
| 宏观/监管数据 | ○ | ◐ 可转换相关网页/Feed | ○ | ● FED/FRED/BLS/CFTC/EIA/SEC 等 | ○ | ◐ 需外接 API 节点 |
| Windows 客户端 | ○ 静态 HTML/脚本 | ○ Node 服务 | ◐ Python Web 服务 | ◐ Tauri 环境管理器，不是阅读器 | ● Electron 阅读器 | ○ Web 编辑器，无原生桌面壳 |
| 插件/Provider | ◐ 接口与固定配置，无动态插件 ABI | ● 约 6500 路由文件的路由体系 | ● Pluggy fetcher/processor | ● Python entry points/Provider | ◐ SDK/IPC，无完整后端插件 ABI | ● built-in/custom/community nodes |
| 可扩展性 | 中 | 高（来源转换） | 高（网页监控） | 高（金融数据） | 中（客户端） | 很高（通用编排） |

## 与目标需求的静态评分

| 项目 | 总分 | 推荐角色 | 一句话结论 |
|---|---:|---|---|
| TrendRadar | 76 | `CORE_FORK_CANDIDATE` | 六仓中最接近采集—降噪—AI—报告—推送主链，但尚不足以成为已批准主底座。 |
| RSSHub | 74 | `SIDE_CAR_SERVICE` | 最适合把没有标准 Feed 的来源转换成受控 RSS，不承担历史库和业务调度。 |
| changedetection.io | 75 | `SIDE_CAR_SERVICE` | 最适合官方网页变化监测，通过 REST/RSS 与主系统解耦。 |
| OpenBB | 69 | `DATA_PROVIDER` | 结构化行情、财报、宏观和监管数据能力最强，不是情报闭环。 |
| Folo | 65 | `UI_REFERENCE` | 阅读器体验成熟，但核心后端缺失、远端依赖和资产许可证阻碍直接 Fork。 |
| n8n | 64 | `WORKFLOW_ENGINE` | 编排能力强，适合作为用户可选 sidecar，不应成为 MVP 核心或随产品嵌入。 |

## 交叉结论

没有一个仓库同时满足“授权来源采集、网页变化、结构化金融数据、长期证据、跨源去重、领域实体/事件、可审计 AI、Windows 阅读器和低风险许可证”。因此最终组合必须保持清晰边界：复用成熟 sidecar 和数据 Provider，只在“即时 AI”中实现不可替代的薄型领域核心、证据模型和适配器契约。

详细证据见各项目的 `ARCHITECTURE.md`、`DATA_FLOW.md`、`REUSABLE_COMPONENTS.md` 和 `FINAL_ASSESSMENT.md`。本矩阵不把文档宣传或未运行功能标为已验证。
