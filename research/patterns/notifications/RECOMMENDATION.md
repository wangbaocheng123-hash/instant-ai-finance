# 建议

MVP 先做应用内收件箱，外部渠道只选一个且默认关闭。唯一键采用 `canonical_event + reason + policy_version`；相同事件更新可合并。消息必须含来源、时间、重要原因和详情链接，明确是研究信息而非交易指令。密钥放 Windows 安全存储，日志脱敏。
