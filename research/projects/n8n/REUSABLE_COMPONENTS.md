# n8n 可复用能力清单

> 所有条目来源：`n8n-io/n8n`；提交：`7968432083cdc2526b3b08983d84d0dc73176356`。
> 归档快照无 `.git`、排除 `.claude`；许可证字段是工程初判，不是法律意见。

## 1. 工作流编排服务

```text
能力名称：可视化 workflow 与执行引擎
源码位置：packages/cli/src/workflow-runner.ts；packages/core/src/execution-engine/workflow-execute.ts
关键类或函数：WorkflowRunner.run/runMainProcess/enqueueExecution；WorkflowExecute.run/runNode/processRunExecutionData
解决的问题：把 trigger、处理、AI、规则、通知组成可观察、可重试的图执行
依赖条件：完整 n8n 服务、DB、节点包；queue 还需 Redis/PostgreSQL/worker
许可证：Sustainable Use License 1.0；调用链可能触及 .ee，需按启用功能复核
推荐复用方式：API_INTEGRATION
复用难度：中（独立服务）/ 很高（嵌入）
是否建议采用：建议作为可选 sidecar，不作为主底座
```

## 2. RSS/HTTP/Schedule 编排模板

```text
能力名称：RSS/HTTP 定时采集链路
源码位置：packages/nodes-base/nodes/RssFeedRead/**；HttpRequest/V3/**；Schedule/ScheduleTrigger.node.ts
关键类或函数：RssFeedRead.execute；RssFeedReadTrigger.poll；HttpRequestV3.execute；ScheduleTrigger.trigger
解决的问题：按周期拉 RSS/HTTP，并把通用 JSON 交给后续节点
依赖条件：网络、目标来源许可、每源错误与字段映射；RSS poll 依赖日期字段
许可证：SUL + 第三方依赖/来源条款
推荐复用方式：API_INTEGRATION
复用难度：低到中
是否建议采用：仅编排授权来源；不替代 RSSHub/正式采集适配层
```

## 3. 流程级跨次去重

```text
能力名称：node/workflow scope 的 processed-data 去重
源码位置：packages/nodes-base/nodes/Transform/RemoveDuplicates/v2/RemoveDuplicatesV2.node.ts；
          packages/core/src/data-deduplication-service.ts；packages/cli/src/deduplication/deduplication-helper.ts
关键类或函数：RemoveDuplicatesV2.execute；DataDeduplicationService.checkProcessedAndRecord；
              DeduplicationHelper.handleHashedItems/handleLatestModes
解决的问题：同批去重和跨 execution 的 hash/latest number/latest date 防重
依赖条件：n8n DB、workflowId；entries history 默认上限 10,000
许可证：SUL
推荐复用方式：DESIGN_REFERENCE
复用难度：低（模式）/高（源码）
是否建议采用：流程内可直接用节点；产品跨源语义去重需自建
```

## 4. Webhook/Public API 桥接

```text
能力名称：双向 HTTP 集成面
源码位置：packages/cli/src/abstract-server.ts；server.ts；public-api/index.ts；
          packages/nodes-base/nodes/Webhook/Webhook.node.ts
关键类或函数：AbstractServer.start；Server.configure；loadPublicApiVersions；Webhook.webhook
解决的问题：外部系统触发 workflow、管理 workflow/execution、把结果回传
依赖条件：auth/API key/session、localhost/network hardening、版本化 API 验证
许可证：服务本体 SUL
推荐复用方式：API_INTEGRATION
复用难度：中
是否建议采用：建议；只依赖 Public API/Webhook，不依赖内部 REST
```

## 5. AI 与 Agent 节点生态

```text
能力名称：多模型、agent、tool、vector store 编排
源码位置：packages/@n8n/nodes-langchain/nodes/agents/**、llms/**、tools/**、vector_store/**
关键类或函数：AgentV3/ToolsAgent V3 helpers；各 LmChat*.supplyData；NodeTypes.getByNameAndVersion
解决的问题：以节点组合 LLM、检索、工具和 agent
依赖条件：外部模型/向量库、API key、提示词与成本/数据出境治理
许可证：SUL + LangChain/模型 SDK/服务条款；部分关联能力可能 .ee
推荐复用方式：API_INTEGRATION
复用难度：中到高
是否建议采用：可做实验编排；正式财经筛选核心不要锁在 n8n workflow
```

## 6. MCP client / tool / server trigger

```text
能力名称：MCP 双向桥接
源码位置：packages/@n8n/nodes-langchain/nodes/mcp/McpClient/McpClient.node.ts；
          McpClientTool/McpClientTool.node.ts；McpTrigger/McpTrigger.node.ts；McpTrigger/McpServer.ts
关键类或函数：McpClient.execute；McpClientTool.supplyData；McpTrigger.webhook；McpServer.setupHandlers
解决的问题：调用外部 MCP tool，或把 n8n workflow tools 暴露为 MCP HTTP endpoint
依赖条件：MCP SDK、session/transport、认证、tool allowlist；queue 还有 relay
许可证：SUL + MCP SDK 原许可证
推荐复用方式：API_INTEGRATION
复用难度：高（安全与会话）
是否建议采用：后置可选；MVP 不默认开放
```

## 7. 节点/凭据加载设计

```text
能力名称：版本化节点 registry 与懒加载
源码位置：packages/cli/src/load-nodes-and-credentials.ts；node-types.ts；packages/core/src/nodes-loader/**
关键类或函数：LoadNodesAndCredentials.init/postProcessLoaders/runDirectoryLoader；
              NodeTypes.getByNameAndVersion
解决的问题：built-in、AI、自定义目录、社区包的统一发现与版本解析
依赖条件：n8n package manifest 与 execution context
许可证：SUL；社区包另有各自许可证
推荐复用方式：DESIGN_REFERENCE
复用难度：中（模式）/很高（代码）
是否建议采用：借鉴 registry/adapter 思想，不复制实现
```

## 8. Editor 交互模式

```text
能力名称：可视化 workflow canvas 与 execution inspect
源码位置：packages/frontend/editor-ui；packages/frontend/@n8n/design-system
关键类或函数：Vue/Pinia/Vue Flow 组件与 stores（非单一入口）
解决的问题：拖拽图、节点参数、运行状态与历史检查
依赖条件：庞大前端 workspace、后端 REST/push 契约
许可证：SUL，且 editor-ui 含 .ee 范围文件
推荐复用方式：DESIGN_REFERENCE
复用难度：高
是否建议采用：只作 UI/交互参考；不作为“即时 AI”桌面壳
```

## 明确拒绝

- `FORK_CORE`：当前 `REJECT`，原因是 SUL/Enterprise 混合、体量、领域错配和升级成本。
- `LIBRARY_DEPENDENCY` 直接嵌入 `n8n-core`/`n8n-workflow`：当前 `REJECT`，授权与耦合不利。
- 复制 `.ee` 文件或功能到产品：`REJECT`。
- 把 n8n DB 当正式财经数据库：`REJECT`。

