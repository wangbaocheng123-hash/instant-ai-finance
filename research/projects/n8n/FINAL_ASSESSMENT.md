# n8n 最终静态评估

> 统一证据：`n8n-io/n8n` @ `7968432083cdc2526b3b08983d84d0dc73176356`，版本 `2.36.0`，
> `master` 官方提交归档副本。无 `.git`，不等于完成克隆；Windows 副本仅排除 `.claude`。

## 结论

推荐角色：`WORKFLOW_ENGINE`，部署方式候选为独立 localhost sidecar；不推荐 `PRIMARY_BASE`、
`CORE_FORK_CANDIDATE` 或产品内嵌库。

n8n 的 workflow engine、节点生态、调度、Webhook/API、AI 和 MCP 都有扎实源码实现，能快速编排
外围自动化。但它没有财经情报核心模型，体量巨大，且 Sustainable Use + `.ee` Enterprise
混合许可显著限制 Fork/嵌入/分发。最合理边界是：用户可选启动 n8n，产品通过受认证的标准 API/
Webhook 交换最小自有 schema；正式采集、规范化、证据、去重、实体、评分和桌面 UI 都归“即时 AI”。

```text
n8n localhost sidecar（可选、独立许可与数据目录）
  -> Public API / Webhook adapter
  -> 即时 AI ingestion boundary
  -> H:\即时AI文件库 的正式 evidence/database
  -> 自有筛选、审计、桌面 UI
```

该建议仍受 `RUNTIME_NOT_ATTEMPTED` 与法律审查约束。

## 100 分制评分

| 评价项 | 分数 | 理由与源码证据 |
|---|---:|---|
| 与财经情报需求匹配度 | 11/20 | workflow/RSS/HTTP/通知重合；无财经实体、事件、证据 schema |
| 已有功能完整度 | 12/15 | workflow、history、auth、API、queue、AI/MCP 完整；非情报产品 |
| 代码可维护性 | 7/10 | TypeScript、分包、测试和 typed config 好；27k 文件/复杂依赖 |
| 扩展和适配能力 | 10/10 | node loader、custom/community、REST/Webhook/Public API/MCP |
| Windows 本地运行能力 | 6/10 | Node/path/scripts 支持意图明确；native 依赖和实际启动未验证 |
| 数据来源能力 | 5/10 | 通用 RSS/HTTP/大量 connector，但无金融来源治理与专业 feed |
| AI 与过滤能力 | 8/10 | 多模型、agent/tool/vector/MCP；领域规则、审计和成本治理需自建 |
| 上游活跃度 | 3/5 | 固定提交结构和版本现代；无 `.git`，不能审计历史/频率 |
| 许可证适配性 | 1/5 | SUL 非宽松开源，且约 1,132 `.ee` 范围文件 |
| 改造成本 | 1/5 | 核心 Fork/桌面化成本极高；仅 sidecar 集成成本可控 |
| **总分** | **64/100** | 静态评分；运行/许可结论可继续下调或确认 |

## 最值得采用

1. `API_INTEGRATION`：可选 n8n sidecar 做跨服务 workflow automation。
2. RSS/HTTP/Schedule/RemoveDuplicates/notification 组成的外围自动化模板。
3. Public API + Webhook 作为稳定边界；MCP 仅后置、受控启用。
4. workflow registry、execution lifecycle、durable scheduling 和 adapter 的设计模式。
5. Editor canvas/execution inspect 仅作 UI 交互参考。

## 不建议采用

- 不 Fork n8n 作为主底座，不把 `n8n-core`/`n8n-workflow` 嵌入正式客户端。
- 不复制任何 node、UI 或 `.ee` 源码到 product。
- 不把 execution DB/binary storage 当正式财经原文与证据数据库。
- 不让 n8n 直接持有券商/交易凭据或发出自动买卖指令。
- MVP 不默认启用 Code、community packages、任意文件访问、MCP server、外网监听。

## 关键证据索引

| 判断 | 源码/标识符 | 状态 |
|---|---|---|
| CLI/服务启动 | `packages/cli/bin/n8n`；`CommandRegistry`；`Start.init/run` | `SOURCE_VERIFIED` |
| 图执行/队列 | `WorkflowRunner.run/runMainProcess/enqueueExecution`；`WorkflowExecute` | `SOURCE_VERIFIED` |
| trigger/webhook/schedule | `ActiveWorkflowManager.add`；`AbstractServer.start`；`DurableScheduler` | `SOURCE_VERIFIED` |
| RSS/去重 | `RssFeedReadTrigger.poll`；`RemoveDuplicatesV2.execute`；`DeduplicationHelper` | `SOURCE_VERIFIED` |
| DB/storage | `DatabaseConfig`；`WorkflowEntity`；`ExecutionEntity/Data`；`StorageConfig` | `SOURCE_VERIFIED` |
| node 扩展 | `LoadNodesAndCredentials`；`NodeTypes`；`CommunityPackagesService` | `SOURCE_VERIFIED` |
| AI/MCP | `nodes-langchain/agents,llms,mcp`；`McpServer/McpClient/McpClientTool` | `SOURCE_VERIFIED` |
| SUL/.ee 分区 | 根 `LICENSE.md` 与 `LICENSE_EE.md` | `SOURCE_VERIFIED` |
| 1500+ integrations | 根 README 宣称，未按独立集成逐项源码核实 | `DOC_ONLY` |
| Windows 成功运行 | 未安装/启动 | `UNVERIFIED_RUNTIME` |

## 最大风险

1. Sustainable Use 与 Enterprise License 对 Fork、嵌入、再分发和商业场景的限制。
2. workflow/Code/HTTP/MCP/credential 权限面过大，默认 SSRF 总开关关闭。
3. 依赖与节点供应链、资源占用、native binding 和升级成本。
4. 通用 execution log 与正式财经证据库混淆，导致 retention、溯源和合规缺口。
5. 运行未验证，Windows 原生安装和 H 盘路径边界仍只是源码预案。

## 尚未确认

- 官方 Git 浅克隆、commit/tree 完整性与上游活跃历史；
- Windows 发布包安装量、启动/内存、native addon、浏览器与停止清理；
- Public API/Webhook 的最小权限和具体 endpoint 实测；
- RSS/RemoveDuplicates/AI/MCP 在目标流程的成功率与审计质量；
- SBOM、依赖许可证、漏洞扫描与最终法律意见；
- “即时 AI”未来分发场景下，用户自行安装 sidecar 是否满足许可边界。

## 下一步（仅供主任务汇总）

将 n8n 标记为 `STATIC_ANALYSIS_COMPLETE / RUNTIME_NOT_ATTEMPTED / CLONE_BLOCKED`。先完成更高优先级
项目运行，再向用户申请 n8n 最小 Windows 发布包试验；未经批准不下载依赖、不构建 monorepo。

