# 通知模式

解决问题：把极少数高价值新事件可靠发送到用户渠道，同时可解释、可重试、可撤销且不重复。

TrendRadar 的 `NotificationDispatcher`、sender 和 splitter 提供多账号、九类渠道、字节分片与报告编排；changedetection 的 Apprise 路径覆盖广并与变化事件绑定；n8n 有大量通知节点和错误工作流；Folo 有桌面/推送交互但依赖远端；RSSHub/OpenBB 无通知核心。

业务模式以 TrendRadar 的分片/多渠道和 changedetection 的事件触发最有价值，但推荐 `REWRITE_FROM_PATTERN` 或使用许可合适的通知库。正式核心拥有 NotificationOutbox，sender 只读取已批准任务，不能自行判断重要度。

建议接口：`enqueue(canonical_id, reason, channel_policy, idempotency_key)` 与 `send(outbox_id)`；记录 payload hash、尝试、响应、下次重试、最终状态。许可证上不复制 TrendRadar GPL/n8n SUL 具体节点。
