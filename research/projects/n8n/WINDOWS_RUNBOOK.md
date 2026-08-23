# n8n Windows 运行手册（静态预案）

> 项目/提交：`n8n-io/n8n` @ `7968432083cdc2526b3b08983d84d0dc73176356`。
> 本轮严格没有安装依赖、没有启动 n8n；下列均是源码推导的 `UNVERIFIED_RUNTIME` 预案。

## Windows 能否直接运行

源码设计支持直接 Node.js 运行：根/CLI package scripts 使用 `scripts/os-normalize.mjs`，CLI 有
`test:win` scripts，路径代码识别 win32，Docker 不是 `start` 命令的强制前提。README 的 Docker
quick start 是 `DOC_ONLY`，且本机 Docker 未安装，不能用它证明 Windows 原生成功。

固定提交要求：

- Node.js `>=24.0.0`；当前环境 `v24.13.1`，版本范围匹配；
- pnpm `>=10.22.0`，packageManager `10.32.1`；当前环境 `10.30.3` 满足 engine 但不同于锁定版本；
- 默认 UI/API 端口 5678；task runner broker 默认 5679；
- Docker 未安装；WSL 无可用发行版确认，二者均不作为本机试验前提。

## 重要：归档不能直接视为可运行发布包

本副本是源码归档，无 `.git`、无预构建 `dist`，且 `.claude` 排除。`packages/cli/bin/n8n` 要求
`../dist/config` 等构建产物；因此不能直接从该目录宣称 `n8n start` 可用。monorepo 安装/构建会
下载大量依赖并可能编译 `isolated-vm`、`sqlite3` 等 native binding，需单独批准。

## 获批后的最小方案（优先发布包，不构建整个仓库）

建议在 `experiments/n8n-lab` 建隔离环境，业务运行数据全部写 H 盘。以下命令只作为候选，
本轮没有执行：

```powershell
$env:N8N_USER_FOLDER = 'H:\即时AI文件库\n8n-runtime'
$env:N8N_STORAGE_PATH = 'H:\即时AI文件库\raw\n8n-storage'
$env:N8N_LISTEN_ADDRESS = '127.0.0.1'
$env:N8N_PORT = '5678'
$env:GENERIC_TIMEZONE = 'Asia/Shanghai'
$env:DB_TYPE = 'sqlite'
$env:EXECUTIONS_MODE = 'regular'
$env:N8N_SSRF_PROTECTION_ENABLED = 'true'
n8n start
```

注意 `getN8nFolder()` 会把实际 n8n folder 设为
`H:\即时AI文件库\n8n-runtime\.n8n`。密钥不可写入脚本或仓库；应在批准后通过安全环境注入
稳定 `N8N_ENCRYPTION_KEY`。

## 验收步骤（获批后）

1. 记录安装下载量、安装目录与 H 盘新增量。
2. 启动后确认只监听 `127.0.0.1:5678`，访问 `/healthz` 和 `/healthz/readiness`。
3. 创建最小 `Schedule -> Set` workflow，确认 execution 存入 H 盘 SQLite。
4. 创建公共测试 RSS 的 `RSS Trigger -> Remove Duplicates -> Set`，确认首次/二次行为。
5. 调用本地 test webhook 和受认证 Public API。
6. 不配置真实账号、付费源或生产 API key；不启用 community node、MCP server、Code node。
7. 停止 Ctrl+C，确认 graceful shutdown；保存日志摘要后清点/清理仅实验产物。

## 停止和清理

- 正常停止：前台 `Ctrl+C`，`BaseCommand.onTerminationSignal()` 等待 active execution、event bus、
  server、DB 和 expression engine 收尾，默认超时 30 秒。
- 不使用强杀，除非进程超过 graceful timeout；记录 PID/端口后再处理。
- 清理前先核对实验路径为明确的 `H:\即时AI文件库\n8n-runtime\.n8n` 与
  `H:\即时AI文件库\raw\n8n-storage`，不得递归删除 H 盘根目录。
- 当前无运行产物，所以本轮无需清理。

## 预期常见问题

| 问题 | 源码依据/处理 |
|---|---|
| 源码归档缺 dist | CLI `bin/n8n` require `../dist/config`；必须构建或用发布包 |
| Node native binding | Dockerfile 显式编译 isolated-vm、sqlite3、Kafka binding；Windows 可能需工具链 |
| pnpm 锁定不同 | `packageManager=10.32.1`，本机 10.30.3；不要擅自全局升级 |
| 端口冲突/Windows 保留端口 | `AbstractServer.init` 对 EADDRINUSE/EACCES 有专门错误 |
| C 盘空间仅约 11 GiB | 禁止无估算安装整个 monorepo；缓存/数据必须规划到 H 盘 |
| queue + SQLite | `BaseCommand.init` 明确警告不受正式支持；最小试验用 regular |
| 文件权限提示 | Windows chmod 语义与 Unix 不同；需验证 settings/config 保护 |
| 浏览器自动打开失败 | `Start.openBrowser` 只提示手动访问，不影响 server |

## 预计成本

快照本身 185.68 MiB；`pnpm-lock.yaml` 包含大量 Node、AI、数据库、浏览器和 native 依赖，完整
monorepo 安装/构建预计达到数 GiB，但未下载，无法给出实测值。必须在运行审批前做发布包与源码
构建两条路径的精确 dry-run/体积估算。

