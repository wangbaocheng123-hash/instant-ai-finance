# 工作流编排模式

解决问题：可靠调度采集、重试、等待、人工审批和外围集成，同时避免通用编排平台侵入正式领域逻辑。

n8n 的 `WorkflowRunner`/`WorkflowExecute`、ActiveWorkflowManager、durable scheduler、regular/queue 和 execution persistence 是六仓最完整的通用实现；changedetection 的 ticker/priority queue/worker 最适合网页 watch；TrendRadar timeline 简单但 `collect=false` 检查晚于抓取保存；RSSHub/OpenBB 请求驱动无通用调度；Folo 的任务执行端在仓库外。

n8n 是最佳通用实现，但因 SUL/Enterprise、体量和安全面只推荐 MVP 后 `WORKFLOW_ENGINE` sidecar。MVP 核心只实现有限的 scheduler + job ledger + outbox，不能复制 n8n 源码，也不能重新制造可视化 workflow 平台。

建议接口：`Job(type, source_id, scheduled_at, idempotency_key, attempts, state)`；状态机只覆盖 queued/running/succeeded/failed/retry/cancelled。复杂用户自动化通过 Public API/Webhook 交给可选 n8n。
