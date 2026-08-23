# n8n 静态研究摘要

> 统一证据标识：`n8n-io/n8n`，默认分支 `master`，固定提交
> `7968432083cdc2526b3b08983d84d0dc73176356`，版本清单 `2.36.0`。
> 研究对象是 `upstream/n8n-snapshot-usable` 官方提交归档副本；它没有 `.git`，不能算完成
> Git 克隆。Windows 解压只排除了无法创建符号链接的 `.claude` 开发辅助目录；归档 SHA-256
> `1B0ECF55F483D8F426B7AAF3E4C03501F63C4570331902483CEA3963358577D5` 已复核一致。

## 它实际解决的问题

n8n 是通用的可视化工作流与 AI-agent 编排平台，不是财经新闻采集器或金融数据库。用户在
Vue 画布中把 trigger、poll、webhook、普通执行节点和 AI tool 连接成图；Express 后端保存
workflow、credential 和 execution，`WorkflowRunner` 决定本进程或 Redis/Bull 队列执行，
`n8n-core::WorkflowExecute` 按连接图调度节点。

源码证据：

- `packages/cli/bin/n8n` 加载配置并调用 `CommandRegistry.execute()`；
- `packages/cli/src/commands/start.ts::Start.init/run` 初始化服务、启动 UI/API、激活 workflow；
- `packages/cli/src/workflow-runner.ts::WorkflowRunner.run/runMainProcess/enqueueExecution`；
- `packages/core/src/execution-engine/workflow-execute.ts::WorkflowExecute.run/runNode/processRunExecutionData`；
- `packages/@n8n/db/src/entities/workflow-entity.ts::WorkflowEntity` 保存 nodes/connections/settings；
- `packages/frontend/editor-ui/package.json` 明示 Vue、Pinia、Vue Flow、Vite 技术栈。

目标用户是需要用低代码方式连接服务、定时任务、Webhook、AI 模型和通知渠道的个人与组织。
README 宣称“1500+ integrations”属于 `DOC_ONLY`；固定提交中实际可审计的两个节点包 manifest
列出 `n8n-nodes-base` 442 个节点、407 种凭据，`@n8n/n8n-nodes-langchain` 122 个节点、38 种凭据。

## 与“即时 AI”的适配性

适合作为可选 `WORKFLOW_ENGINE` / 独立 sidecar，不适合作为 `PRIMARY_BASE`。

它能快速编排“定时或 RSS/Webhook → HTTP/RSS 数据 → 转换/跨次去重 → AI 节点 →
Telegram/Slack/Email”等流程，也有执行历史、重试、凭据加密、API 与 MCP。但它没有财经
实体/事件/来源可信度模型、证据原文库、跨来源新闻规范化、领域重要度评分或 Windows 原生
桌面壳。正式情报数据库与可审计筛选仍需在“即时 AI”侧实现。

需求重合估计约 45%：编排、调度、连接器、Webhook、AI 和通知重合较高；财经来源治理、
内容模型、证据库、低噪声评分与桌面体验重合较低。该比例是基于源码能力映射的工程估计，
不是运行测量。

## 最强的五项能力

1. `WorkflowExecute` 的图执行、等待/恢复、重试、错误分支、部分执行和取消语义。
2. 节点生态及 `LoadNodesAndCredentials` 的 built-in、LangChain、自定义目录、社区包加载机制。
3. trigger/poll/schedule/webhook 统一激活，含数据库型 durable scheduler 与执行幂等键。
4. regular 与 queue 两种执行形态；`Worker`、`Webhook` 命令可拆分扩展。
5. AI-agent、模型、向量库、MCP client/tool/server trigger 与普通节点转 AI tool 的能力。

## 最大的五项问题

1. 根许可证是 Sustainable Use License 1.0，不是宽松开源许可证；`.ee` 文件另受 Enterprise
   License 约束，Fork、嵌入、分发和未来商业化边界很不利。
2. 27,108 文件、185.68 MiB 源码和庞大 pnpm monorepo，维护、构建、升级与供应链面很大。
3. 它是通用编排器，不提供财经情报数据模型或证据链，不能替代正式核心。
4. 用户可配置 HTTP、Code、文件、credential 和社区节点，默认 SSRF 防护关闭，必须强硬化。
5. 当前仅静态研究；Windows 上安装体积、原生依赖、稳定性、资源占用和节点实际成功率未知。

## 状态

- 静态源码分析：`COMPLETE`
- 运行验证：`NOT_ATTEMPTED`
- Git 克隆：`BLOCKED`（官方归档副本不等于克隆）
- 上游活跃度：快照无 `.git` 历史，`UNVERIFIED_FROM_SNAPSHOT`

