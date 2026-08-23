# n8n 模块地图

> `n8n-io/n8n` @ `7968432083cdc2526b3b08983d84d0dc73176356`；官方提交归档，
> 无 `.git`，Windows 分析副本排除 `.claude`。

| 模块 | 作用/入口 | 主要依赖 | 独立性与复用判断 | 难度 | 许可证影响 |
|---|---|---|---|---|---|
| `packages/cli` | CLI、Express、auth、workflow/execution、webhook、scheduler；`bin/n8n` | core/workflow/db/config/大量服务 | 产品运行核心，高度耦合；只建议独立进程 | 高 | Sustainable Use；内部含大量 `.ee` |
| `packages/core` | `WorkflowExecute`、node contexts、binary、encryption、loaders | workflow/config/DI | 理论可库化但与 CLI/DB hooks 强耦合 | 很高 | Sustainable Use；S3/Azure `.ee` |
| `packages/workflow` | Workflow/Node 类型、表达式、图模型 | 较底层 | 最独立的模型层，但直接嵌入仍受许可 | 中 | Sustainable Use |
| `packages/@n8n/db` | TypeORM entities、repositories、migrations | TypeORM/SQLite/Postgres | 可理解 schema，不应复制进产品 | 高 | Sustainable Use；29 个 `.ee` 范围文件 |
| `packages/@n8n/config` | typed env config 与 decorators | zod | 设计可参考；不值得直接依赖 | 中 | Sustainable Use |
| `packages/@n8n/scheduler` | 数据库型 durable scheduler | DB abstraction | 模式有价值，直接拆出需重构 | 高 | 以文件标记为准，非 `.ee` 主体受 SUL |
| `packages/nodes-base` | 442 个节点、407 credential manifest 条目 | 大量 SDK | 节点生态强，但单节点依赖/条款各异 | 中到高 | SUL + 第三方依赖；少量 `.ee` |
| `packages/@n8n/nodes-langchain` | 122 AI/MCP 节点、38 credentials | LangChain/模型/MCP/向量库 | 作为 n8n 内节点使用；不建议搬出 | 高 | SUL + 第三方依赖 |
| `packages/frontend/editor-ui` | Vue workflow editor | Vue/Pinia/Vue Flow/Vite | UI/交互模式参考；不作桌面 UI 底座 | 高 | SUL；包含 `.ee` 范围文件 |
| `packages/frontend/@n8n/design-system` | UI 组件和主题 | Vue/Element Plus/Reka | 设计参考，不建议嵌入 | 中 | manifest SUL |
| `packages/@n8n/blob-storage` | FS/JSON/S3/Azure byte stores | cloud SDK | FS 模式可学习；S3/Azure 为 `.ee` | 中 | 混合：`.ee` 单独授权 |
| `packages/@n8n/task-runner` / CLI task-runners | Code/Python 隔离执行 | broker/child processes | 安全模式可参考；直接复用耦合高 | 高 | SUL，配置需审计 |
| `packages/modules/**` | feature modules，动态加载 | backend registry | 边界不断演进，含 enterprise 功能 | 高 | 按 `.ee` 判定 |
| `packages/extensions/**` | 外部/运行扩展 | 各自依赖 | 逐包评估 | 中 | 逐文件与第三方许可证 |

## 主调用关系

```text
cli Start
  -> @n8n/config + @n8n/db
  -> core Loaders -> nodes-base + nodes-langchain + custom/community
  -> Server + ActiveWorkflowManager + DurableScheduler
  -> WorkflowRunner
  -> core WorkflowExecute
  -> workflow graph/types/expression
  -> DB + execution/binary storage + event/push/telemetry
```

## 独立性结论

n8n 的“节点类”看似插件，但真正执行依赖 `IExecuteFunctions`、credential helper、node context、
binary/persistence、expression 与 lifecycle hooks。把单个节点源码复制到“即时 AI”并不是低成本
复用；更稳妥的是保持 n8n 独立、通过 REST/Webhook 调 workflow，或只参考接口和模式。

## 许可证范围量化

本快照共 27,108 个文件；按根 `LICENSE.md` 的规则扫描，文件名含 `.ee.` 或路径段含 `.ee`
的文件约 1,132 个。该数字用于提醒混合授权面，不代替逐文件法律审查。

