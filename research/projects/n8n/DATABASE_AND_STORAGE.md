# n8n 数据库与存储

> 项目/提交：`n8n-io/n8n` @ `7968432083cdc2526b3b08983d84d0dc73176356`。研究副本是
> 官方提交归档，无 `.git`、不等于完成克隆；Windows 解压仅排除 `.claude` 开发辅助目录。

## 数据库

`packages/@n8n/config/src/configs/database.config.ts::DatabaseConfig` 只接受：

- `sqlite`（默认），`DB_SQLITE_DATABASE=database.sqlite`，默认 pool size 3；
- `postgresdb`，默认 host localhost、port 5432、database n8n、schema public。

`BaseCommand.init()` 在服务启动前执行 `DbConnection.init()` 与 `migrate()`。queue 模式源码警告
SQLite 不受正式支持，应使用 PostgreSQL；Redis/Bull 是执行队列，不是 workflow 主数据库。

## 主要数据模型

| 模型 | 源码 | 关键字段/结论 |
|---|---|---|
| Workflow | `@n8n/db/src/entities/workflow-entity.ts::WorkflowEntity` | name、nodes(JSON)、connections(JSON)、settings、staticData、pinData、versionId、activeVersionId |
| Workflow 版本 | `workflow-history.ts`、`workflow-published-version.ts` | workflow 历史与发布版本 |
| Execution | `execution-entity.ts::ExecutionEntity` | status/mode/time/workflowId/waitTill/storedAt/deduplicationKey/size/version |
| Execution payload | `execution-data.ts::ExecutionData` | 序列化 `data`、workflow snapshot、workflowVersionId |
| Webhook | `webhook-entity.ts::WebhookEntity` | method/path/workflow/node；支持动态 path/cacheKey |
| Credential | `credentials-entity.ts::CredentialsEntity` | name/type/encrypted text data 与共享关系 |
| 去重状态 | `processed-data.ts` + `DeduplicationHelper` | workflowId/context + hashed entries/latest key/latest date |
| 调度 | `scheduled-job.ts`、`scheduled-task.ts` | durable scheduler jobs/tasks/lease/retry |
| Tags/folders/projects | 同目录对应 entities | UI 与访问组织模型 |

## 执行与二进制存储

`packages/core/src/storage.config.ts::StorageConfig`：

- execution data 默认 `database`；可选 `filesystem`、`s3`、`azure`；
- filesystem 默认 `~/.n8n/storage`，可用 `N8N_STORAGE_PATH` 指定；
- entity `ExecutionEntity.storedAt` 用 `db/fs/s3/az` 标记位置。

`packages/core/src/binary-data/binary-data.config.ts::BinaryDataConfig`：

- regular 模式 binary 默认 `filesystem`，queue 默认 `database`；
- 路径优先 `N8N_BINARY_DATA_STORAGE_PATH`，其次 `N8N_STORAGE_PATH`，再 `~/.n8n/storage`；
- database 模式单文件最大值默认 512 MiB；
- S3 和 Azure 实现文件带 `.ee`，生产使用受 Enterprise License/feature check 约束。

`packages/@n8n/blob-storage/src/fs-byte-store.ts` 与 `json-store.ts` 提供文件 byte/json store；
`BaseCommand.initBinaryDataService()` 装配 database manager、S3/Azure manager 和 execution JSON store。

## 凭据

`CredentialsEntity.data` 是 text；`packages/core/src/encryption/cipher.ts::Cipher` 用 instance
encryption key 加解密（兼容 AES-256-CBC，密钥轮换路径可用 AES-256-GCM 包装 DEK）。这说明
凭据不是明文业务字段，但安全取决于 `.n8n/config` / `N8N_ENCRYPTION_KEY` 的保护和备份一致性。

## 新闻、原链、去重、历史查询

- 新闻没有专用实体；RSS/HTTP 输出只作为通用 execution JSON。
- 原始链接能否保留取决于 workflow 是否映射并写出 URL，不是平台强制约束。
- `RemoveDuplicatesV2` 支持同批与跨 execution 去重；跨次状态由 `processed_data` 保存。
- execution、workflow history 和 API 支持历史查看/查询，但不是财经文章检索库。
- 默认 pruning：完成 execution 最大年龄 336 小时、最大计数 10,000（可配置）；因此 execution
  日志不适合长期证据保存。

## 迁移与 Windows 适配

SQLite 对单机 Windows 最省事，`N8N_USER_FOLDER` 后会再追加 `.n8n`。若批准运行，应把
`N8N_USER_FOLDER` 指向 `H:\即时AI文件库\n8n-runtime`，数据库将位于其 `.n8n` 子目录；
同时显式设 `N8N_STORAGE_PATH=H:\即时AI文件库\raw\n8n-storage` 之类的隔离目录。
这是建议配置，`UNVERIFIED_RUNTIME`，还需遵循项目 H 盘布局决策。

SQLite → PostgreSQL 的正式迁移工具/可靠性未在本静态子任务中验证；不得仅凭 TypeORM 同时支持
两种 DB 就声称可无损迁移。

## 结论

n8n 存储适合 workflow 定义、凭据和运行审计，不适合充当“即时 AI”的正式原文/证据/新闻库。
产品应通过 API/adapter 将规范化结果和证据写入 H 盘正式数据层，并将 n8n execution 保持为
可清理的编排运行记录。
