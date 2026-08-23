# n8n 配置地图

> `n8n-io/n8n` @ `7968432083cdc2526b3b08983d84d0dc73176356`；仅静态验证。研究副本是
> 官方提交归档，无 `.git`、不等于完成克隆；Windows 解压仅排除 `.claude` 开发辅助目录。

## 配置机制

`packages/@n8n/config/src/decorators.ts` 与 `GlobalConfig`/`configs/*.config.ts` 用 `@Env`
把环境变量解析为 typed config。`packages/cli/bin/n8n` 先加载 dotenv，再加载 dist config，确保
TypeORM entity decorator 看到已确定的 DB 类型。也支持 `N8N_CONFIG_FILES`，具体优先级需在运行时
验证。

## 最小本机相关配置

| 配置 | 源码/默认 | “即时 AI”建议（未运行） |
|---|---|---|
| `N8N_USER_FOLDER` | `config/src/utils/utils.ts::getN8nFolder`；再追加 `.n8n` | 指向 H 盘隔离父目录，避免 C 盘和 Git |
| `N8N_PORT` | `GlobalConfig.port=5678` | 保持或选择空闲本机端口 |
| `N8N_LISTEN_ADDRESS` | 默认 `::` | 独立 sidecar 应显式 `127.0.0.1` |
| `N8N_PROTOCOL` | 默认 `http` | localhost 可先 http；暴露网络必须另审 |
| `DB_TYPE` | 默认 `sqlite` | 单机试验 SQLite；queue 不用 SQLite |
| `DB_SQLITE_DATABASE` | 默认 `database.sqlite` | 放入 H 盘 n8n runtime；不要产品正式 DB |
| `N8N_STORAGE_PATH` | 默认 `<n8nFolder>/storage` | 指向 H 盘隔离目录 |
| `N8N_DEFAULT_BINARY_DATA_MODE` | regular filesystem；queue database | 单机试验 filesystem |
| `EXECUTIONS_MODE` | 默认 `regular` | 单机试验 regular；避免 Redis/Postgres |
| `GENERIC_TIMEZONE` | 默认 `America/New_York` | 显式 `Asia/Shanghai` |
| `EXECUTIONS_DATA_*` | `executions.config.ts` | 限制保存/保留期，避免重复存整篇原文 |
| `N8N_ENCRYPTION_KEY` | `InstanceSettings` 使用 | 只放环境/密钥管理，绝不入仓库 |

## API、Webhook 和 UI

- `N8N_PATH`、`N8N_HOST`、`N8N_EDITOR_BASE_URL`、`WEBHOOK_URL/N8N_WEBHOOK_URL` 控制外部 URL；
- `N8N_PUBLIC_API_DISABLED` 与 `PublicApiConfig` 控制 API-key auth；源码说明 Public API routes
  始终注册，session cookie 仍可用；
- `N8N_DISABLE_UI`/endpoint config 控制 UI；
- `N8N_METRICS` 默认 false，health/readiness 由 `AbstractServer.setupHealthCheck()` 提供；
- webhook/form/MCP endpoint 由 `EndpointsConfig` 和 `AbstractServer` 组合。

## 调度、队列与 worker

- `EXECUTIONS_MODE=regular|queue`；queue 使用 Redis/Bull，并应使用 PostgreSQL；
- `N8N_CONCURRENCY_PRODUCTION_LIMIT`，worker CLI `--concurrency` 默认 10；
- `N8N_SCHEDULER_ENABLED` 等由 `scheduler.config.ts::SchedulerConfig` 控制 durable scheduler；
- poll trigger 是否走 durable scheduler 还受 `N8N_SCHEDULER_POLL_TRIGGERS_ENABLED` 控制；
- task runner 默认 internal，broker `127.0.0.1:5679`，task timeout 默认 5 分钟。

## 节点与扩展

- `NodesConfig.include/exclude` 可限制节点；
- `N8N_CUSTOM_EXTENSIONS` 用分号分隔，自定义目录加载；默认 custom 目录在 `.n8n/custom`；
- community package 是否启用、是否允许 unverified、registry/token 等在
  `community-packages.config.ts`；
- 安装社区包会访问 npm 并写 `.n8n/nodes`，不应在未批准的运行中启用。

## 安全关键项

- `N8N_RESTRICT_FILE_ACCESS_TO` 默认 `~/.n8n-files`；`N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES=true`；
- `N8N_SSRF_PROTECTION_ENABLED=false` 是危险的兼容默认，正式接收不可信 workflow/URL 时应开启；
- `N8N_SSRF_BLOCKED_IP_RANGES` 默认含 private/loopback/link-local，但只有保护启用且调用点 opt-in
  时生效；
- `N8N_RUNNERS_AUTH_TOKEN`、外部 runner、proxy hops、TLS、cookie/auth 均需部署审查；
- telemetry/PostHog/Sentry 需按隐私与出站策略核查，不应假定自托管即零遥测。

## AI / MCP

`ai.config.ts`、`agents.config.ts`、`mcp-client.config.ts`、`mcp-server.config.ts` 等控制平台能力；
模型 API key 通常通过相应 credential node 保存。MCP server/client 暴露工具，必须限制 workflow、
auth、网络与 credential scope。

## 配置状态

所有变量名/默认值为 `SOURCE_VERIFIED`；建议值为 `PROPOSED_NOT_APPLIED`。本轮没有创建 `.env`、
没有写密钥、没有启动进程。
