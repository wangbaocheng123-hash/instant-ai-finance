# n8n 扩展点

> 项目/提交：`n8n-io/n8n` @ `7968432083cdc2526b3b08983d84d0dc73176356`。研究副本是
> 官方提交归档，无 `.git`、不等于完成克隆；Windows 解压仅排除 `.claude` 开发辅助目录。

| 扩展点 | 源码/标识符 | 能力 | 对“即时 AI”的建议 |
|---|---|---|---|
| 自定义节点目录 | `LoadNodesAndCredentials.getCustomDirectories/loadNodesFromCustomDirectories` | `.n8n/custom` 与 `N8N_CUSTOM_EXTENSIONS` | 仅独立 n8n 实例内使用；不复制到产品核心 |
| 社区节点包 | `CommunityPackagesService.installOrUpdatePackage` | npm pack、checksum、ignore scripts install、load/rollback | 默认禁未验证包；供应链白名单 |
| Node loader | `n8n-core::DirectoryLoader/PackageDirectoryLoader/LazyPackageDirectoryLoader` | 从 package manifest 懒加载 node/credential class | 强但耦合；设计参考 |
| Module registry | `BaseCommand.moduleRegistry`、`CommandRegistry.moduleRegistry` | module 可注册 command、node loader、controller/hook | 含内部/enterprise 边界，暂不依赖 |
| 节点接口 | `n8n-workflow::INodeType`；`WorkflowExecute.runNode` | execute/poll/trigger/webhook/supplyData/declarative request | 自建 adapter 可模仿最小接口 |
| 普通节点变 AI tool | `NodeTypes.getByNameAndVersion`、`convertNodeToAiTool` | `usableAsTool` 节点合成为 Agent tool | 模式参考，需权限裁剪 |
| REST | `ControllerRegistry.activate` + decorators | 内部 UI REST controller | 不把内部 REST 当稳定外部契约 |
| Public API | `public-api/index.ts::loadPublicApiVersions` + `v1/openapi.yml` | workflow/execution/credential/tag/data-table 等 API | 首选标准集成面；实际 endpoint/权限需运行验证 |
| Webhook | `AbstractServer.start`、`Webhook.node.ts::webhook` | production/test/waiting/form/webhook route | 可由“即时 AI”调用 n8n workflow |
| MCP server | `McpTrigger.webhook` + `McpServer` | workflow 工具暴露为 MCP server endpoint | 高风险可选扩展；必须认证/白名单 |
| MCP client/tool | `McpClient.execute`、`McpClientTool.supplyData` | 调 MCP tool，或供 AI Agent 使用 | 可连受控本机 MCP 服务 |
| External hooks | `BaseCommand.initExternalHooks`、`ExternalHooks.run` | `n8n.ready/stop`、workflow postExecute 等 | 内部扩展点，不如 Public API 稳定 |
| CLI | `CommandRegistry` + `commands/**` | start/worker/webhook/execute/import/export | 自动化部署与备份；非产品 API |
| RSS | `RssFeedRead` / `RssFeedReadTrigger` | 抓取或轮询 RSS | 适合作为 RSSHub 等 sidecar 的消费器 |
| 数据去重 API | `IExecuteFunctions.helpers.checkProcessedAndRecord` → service/helper | node/workflow scope 跨次状态 | 只作流程防重；正式去重在产品侧 |
| DB repositories | `@n8n/db/src/repositories/**` | workflow/execution/webhook/schedule persistence | 内部实现，不直接读写其表 |

## 节点包约定

`packages/nodes-base/package.json::n8n.nodes/credentials` 和 LangChain 同名字段列出构建后的 class
路径。`LoadNodesAndCredentials.postProcessLoaders()` 把 packageName 与 nodeName 合成全名，建立 known/
types registry；`NodeTypes.getByNameAndVersion()` 再解析版本、declarative routing 或 synthetic tool。

这表明最原生的功能扩展方式是写 n8n node package。但对本项目而言，定制节点会把产品业务逻辑
锁进 SUL 平台与其执行上下文。第一选择仍应是标准 HTTP/Webhook/Public API adapter；只有通用、
可替换的薄节点才考虑进入独立 n8n 扩展包。

## 未发现/边界

- 没有独立财经 provider SPI、新闻 schema 或 source governance plugin。
- `Database` repository 是内部接口，不是跨产品数据 adapter。
- README 的 “1500+ integrations” 为 `DOC_ONLY`；本报告只把 package manifest 的 564 个 node
  path（442+122）作为固定提交可审计计数，且 path 数不等于独立服务数或当前可用率。
