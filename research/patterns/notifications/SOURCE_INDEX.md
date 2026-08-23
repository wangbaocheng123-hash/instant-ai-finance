# 来源索引

| 项目/提交 | 源码 | 能力 |
|---|---|---|
| TrendRadar `8ee2602` | `notification/dispatcher.py`; `senders.py`; `splitter.py`; `batch.py` | 多渠道、多账号、分片 |
| changedetection `fce2478` | `notification_service.py`; `notification/handler.py`; Flask runner | 变化事件 → queue → Apprise |
| n8n `7968432` | Telegram/Slack/Email 等 nodes；error workflow | 通用渠道和流程重试 |
| Folo `7c220c6` | Electron notification/push managers | 客户端体验，远端依赖 |
