# 即时 AI 正式产品区

状态：`LOCAL_MVP_USABLE / CONTINUING_DEVELOPMENT`。

当前版本是 Windows 本地桌面 MVP：Python 标准库提供 localhost 服务，SQLite/WAL 保存正式元数据，Edge 应用窗口提供桌面界面。没有账户层，也不需要姓名或邮箱。

启动入口：

```powershell
pythonw product\launch_instant_ai.py
```

桌面快捷方式已经创建，可直接双击 `即时 AI`。客户端提供今日重点、全部情报、收藏、专题、重要提醒、来源状态、证据详情、搜索、导出、备份和恢复验证。

正式业务数据只写入 `H:\即时AI文件库`：

- `database\instant_ai.db`：正式 SQLite 数据库；
- `raw\`：按 SHA-256 保存的来源原始响应；
- `evidence\`：采集运行清单与恢复演练审计报告；
- `exports\`：人工导出的 CSV；
- `backups\`：备份；
- `cache\`：桌面壳和可再生缓存；
- `logs\`：脱敏日志。

AI 后处理目前已完成证据包和任务边界，但尚未配置真实模型。点击“准备 AI 证据包”只会保存可审计输入并等待安全配置，不会把确定性评分冒充模型摘要，也不会阻断现有采集和阅读。

验证命令：

```powershell
scripts\verify-instant-ai.ps1
```

代码、测试和静态界面保留在本目录；数据库、原始证据、缓存和日志不会进入 Git。
