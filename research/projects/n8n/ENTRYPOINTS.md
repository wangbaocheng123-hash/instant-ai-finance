# n8n 入口清单

> `n8n-io/n8n` @ `7968432083cdc2526b3b08983d84d0dc73176356`，归档快照、无 `.git`、无 `.claude`。

| 类别 | 入口与标识符 | 真实作用 | 状态 |
|---|---|---|---|
| npm/CLI | `packages/cli/package.json::bin.n8n` → `packages/cli/bin/n8n` | 校验 Node engine、加载 dotenv/config，调用 `CommandRegistry.execute()` | `SOURCE_VERIFIED` |
| 命令注册 | `packages/cli/src/command-registry.ts::CommandRegistry` | 默认 `start`；按 `commands/<name>.js` 和 modules 动态发现命令 | `SOURCE_VERIFIED` |
| 主服务 | `packages/cli/src/commands/start.ts::Start` | Web UI/API、active workflows、pruning、scheduler | `SOURCE_VERIFIED` |
| HTTP | `packages/cli/src/abstract-server.ts::AbstractServer.init/start` | 创建 HTTP/HTTPS server，默认监听 `N8N_PORT=5678` | `SOURCE_VERIFIED` |
| 应用装配 | `packages/cli/src/server.ts::Server.configure` | Public API、controller registry、push、event bus、editor assets | `SOURCE_VERIFIED` |
| REST | `packages/cli/src/controller.registry.ts::ControllerRegistry.activate` | 激活装饰器 controller；workflow 在 `workflows/workflows.controller.ts`，execution 在 `executions/executions.controller.ts` | `SOURCE_VERIFIED` |
| Public API | `packages/cli/src/public-api/index.ts::loadPublicApiVersions` | 从 `public-api/v1/openapi.yml` 建 API router；API key 或 session 策略 | `SOURCE_VERIFIED` |
| Webhook | `packages/cli/src/abstract-server.ts::AbstractServer.start` | `/webhook/*`、`/test-webhook/*`、forms、waiting、MCP HTTP | `SOURCE_VERIFIED` |
| 独立 Webhook | `packages/cli/src/commands/webhook.ts::Webhook` | queue 模式下只接 production webhook | `SOURCE_VERIFIED` |
| Worker | `packages/cli/src/commands/worker.ts::Worker` | Redis 队列 worker，默认并发 10 | `SOURCE_VERIFIED` |
| 单次执行 | `packages/cli/src/commands/execute.ts` | CLI 执行 workflow | `SOURCE_VERIFIED` |
| 批执行 | `packages/cli/src/commands/execute-batch.ts` | 批量 CLI execution | `SOURCE_VERIFIED` |
| 导入/导出 | `packages/cli/src/commands/import/**`、`export/**` | workflow、credential、entity 导入导出 | `SOURCE_VERIFIED` |
| 定时激活 | `active-workflow-manager.ts::add/addNonWebhookTriggers` | 激活 poll、active trigger、schedule cron | `SOURCE_VERIFIED` |
| durable 调度 | `scheduling/durable-scheduler.ts::DurableScheduler.start` | `Start.run()` 启动数据库型 scheduler（配置开启才生效） | `SOURCE_VERIFIED` |
| Docker | `docker/images/n8n/Dockerfile` → `docker-entrypoint.sh` | Linux image，暴露 5678，最终 exec `n8n` | `SOURCE_VERIFIED` |

## 可见 CLI 命令源码

`start`、`worker`、`webhook`、`execute`、`execute-batch`、`audit`、`db:revert`、workflow
publish/unpublish/update/list、credential/workflow/entity import/export、license info/clear、MFA/LDAP/user
管理。命令最终是否可用还可能受 module、config 和 license 控制。

## 关键配置入口

- 根 `package.json`：版本 `2.36.0`，Node `>=24.0.0`，pnpm `>=10.22.0`，workspace start/build/test。
- `packages/@n8n/config/src/index.ts::GlobalConfig`：host、port 5678、listen address `::`、protocol。
- `packages/@n8n/config/src/configs/*.config.ts`：DB、execution、endpoint、node、runner、scheduler、
  SSRF、security、AI/MCP 等 typed env 配置。
- `packages/@n8n/config/src/utils/utils.ts::getN8nFolder`：Windows 用 `N8N_USER_FOLDER` 或
  `USERPROFILE`，再追加 `.n8n`。
- `packages/core/src/storage.config.ts::StorageConfig` 与
  `binary-data/binary-data.config.ts::BinaryDataConfig`：execution/binary 文件位置。
- `.env.local.example`、`.env.eval.example`：示例，不能当生产安全基线。

## 启动命令（仅从源码推导，未执行）

```powershell
# 发布包形态；需先有匹配版本的 n8n 包及依赖
n8n start
n8n worker --concurrency=10
n8n webhook

# monorepo 开发形态；会安装/构建大量依赖，本轮禁止执行
pnpm start
pnpm dev:up
```

上述命令是 `SOURCE_VERIFIED_COMMAND / UNVERIFIED_RUNTIME`，不是本轮运行结果。

