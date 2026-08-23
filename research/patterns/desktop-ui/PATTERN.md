# Windows 桌面界面模式

解决问题：让个人用户在 Windows 上高效阅读、筛选、搜索和核验证据，同时安全管理本地服务与 H 盘数据。

Folo 的 Electron/React 时间线、阅读器、搜索、状态/缓存分层和 Windows 打包最成熟，是最佳 `UI_REFERENCE`；但其核心后端依赖远端 API，本地缓存会裁剪，窗口关闭 sandbox/context isolation 并启用 Node integration/webview，且 `icons/mgc` 不可再分发。OpenBB 的 Tauri/React 只证明本地环境/后端管理模式，不是情报阅读 UI。TrendRadar 是静态 HTML，n8n 是 workflow editor，changedetection 是 Web 管理界面，RSSHub 无客户端。

推荐客户端形式：localhost Web UI + 薄桌面壳候选。R0 不锁定 Tauri/Electron/PySide6；先用同一最小界面做安装体积、内存、启动、更新、托盘、深链和安全对比。采用 `DESIGN_REFERENCE` / `REWRITE_FROM_PATTERN`，不复制 Folo 代码或资产。

建议 UI/API 边界只暴露业务 DTO 和文件打开命令；renderer 无 Node/文件系统权限，外部链接交系统浏览器，所有 privileged IPC 白名单化。
