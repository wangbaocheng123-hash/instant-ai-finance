# n8n 运行与静态验证结果

> 项目/提交：`n8n-io/n8n` @ `7968432083cdc2526b3b08983d84d0dc73176356`。

## 结论

运行未尝试（`NOT_ATTEMPTED`）。本项目指令把 n8n 排在第三运行优先级；本子任务被明确限制为
静态分析，不安装依赖、不启动服务。因此不存在启动截图、运行日志、抓取结果、保存结果、UI
截图或 API 调用成功证据。

| 验证项 | 结果 | 证据/说明 |
|---|---|---|
| 官方固定提交归档下载 | `PASS` | H 盘缓存 tar.gz；SHA-256 复核为 manifest 值 |
| 可分析副本 | `PASS_WITH_LIMITATION` | 27,108 文件、194,700,632 bytes（185.68 MiB） |
| Git clone | `FAIL/BLOCKED` | 副本无 `.git`，不含 remote/history，不能算完成克隆 |
| `.claude` | `EXCLUDED` | Windows 无法创建归档内符号链接；仅排除该开发辅助目录 |
| 根许可证存在 | `PASS` | `LICENSE.md`、`LICENSE_EE.md` 已读并复核分区 |
| 版本/engine 静态匹配 | `PASS_STATIC` | package 2.36.0；Node >=24、pnpm >=10.22；本机版本范围匹配 |
| 依赖安装 | `NOT_ATTEMPTED` | 未产生 node_modules |
| 构建 | `NOT_ATTEMPTED` | 源码归档无可依赖的完整 dist |
| 成功启动 | `NOT_ATTEMPTED` | 无进程、无日志、无截图 |
| 成功抓到数据 | `NOT_ATTEMPTED` | 未联网调用任何数据源 |
| 成功保存数据 | `NOT_ATTEMPTED` | 未创建 n8n SQLite/storage |
| 成功展示 UI | `NOT_ATTEMPTED` | 未打开 5678 |
| 成功调用 API/Webhook/MCP | `NOT_ATTEMPTED` | 未启动端口 |
| 新增运行磁盘占用 | `0 MiB` | 本子任务未安装/运行；只生成 Markdown 研究报告 |
| 大量无关依赖 | `NOT_PRODUCED` | lockfile 显示风险，但没有实际下载 |

## 静态检查事实

- 根 package version `2.36.0`，Node `>=24.0.0`，pnpm `>=10.22.0`；环境盘点 Node
  `v24.13.1`、pnpm `10.30.3`。
- n8n-nodes-base manifest：442 node paths、407 credential paths；LangChain：122/38。
- 默认端口 5678，默认 DB SQLite，regular 模式；源码包含 Windows test scripts 和 path 分支。
- 源码 Dockerfile 显式编译 `isolated-vm`、`sqlite3`、Kafka binding，提示本机 native 依赖成本。
- 快照排除 `.claude` 不影响本报告读取的 runtime、workflow、node、DB、API、AI/MCP 路径；但
  仍应在完整 Git clone 后复核文件树与提交状态。

## 阻塞与批准需求

1. 正式 Git 浅克隆仍受 GitHub 通路/凭据阻塞。
2. 运行要下载发布包或安装 monorepo 依赖，可能数 GiB，并涉及 native build；需用户批准。
3. Docker 未安装、WSL 无可用发行版；本机 Node 方案优先，不应自动安装 Docker/WSL。
4. 运行前要准备 H 盘隔离路径、稳定加密 key、localhost bind、SSRF 与节点 allowlist。

## 建议的后续运行验收

获批后优先验证“发布包 + regular + SQLite + localhost + H 盘”，而非完整 monorepo 构建。最小流
只用 `Schedule/RSS Test Feed -> RemoveDuplicates -> Set -> local webhook response`；不配置真实
财经账号、模型 key、community node、MCP server 或通知账号。记录下载量、启动耗时、空闲内存、
H 盘写入、API/health、两次去重及停止清理。

