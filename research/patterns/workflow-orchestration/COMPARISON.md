# 实现比较

n8n 强在任意图和连接器，代价是平台、许可证和权限面；changedetection 强在固定 watch 生命周期；TrendRadar 强在简单日报时段，却需要修正采集门控。即时 AI 的核心任务形态有限，先采用显式 job type 比通用 DAG 更安全、可测试。
