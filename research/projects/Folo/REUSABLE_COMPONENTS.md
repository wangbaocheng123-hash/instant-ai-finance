# 可复用组件决策

> 项目：`RSSNext/Folo`；提交：`7c220c69a841defbfeeb00a86ed75ad482b22a57`；来源为无 `.git` 的 `OFFICIAL_ARCHIVE_SNAPSHOT`。以下“复用”优先指设计复用，不授权直接复制代码。

## 决策表

| 能力 | 证据 | 复用方式 | 难度 | 依赖/许可证 | 建议 |
|---|---|---|---|---|---|
| 时间线、三栏阅读器、订阅管理交互 | `apps/desktop/layer/renderer/src/pages`、`modules/entry-content` | `DESIGN_REFERENCE` | 中 | Folo 源码 AGPL；视觉资产需逐项审计 | 推荐研究，不复制 |
| 桌面壳生命周期与窗口/托盘/深链分层 | `apps/desktop/layer/main/src/manager/*` | `DESIGN_REFERENCE` | 中 | Electron；现有安全配置不可继承 | 参考职责划分 |
| API DTO → morph → store → SQLite hydration | `packages/internal/store`、`packages/internal/database` | `REWRITE_FROM_PATTERN` | 高 | AGPL；必须独立实现证据库 | 仅借鉴模式 |
| 本地 Fuse 搜索 | `apps/desktop/layer/renderer/src/store/search/index.ts` | `LIBRARY_DEPENDENCY` | 低 | 应直接评估 Fuse.js 自身许可证，不复制 Folo glue code | 可选 |
| 全文可读性解析 | `packages/readability` | `LIBRARY_DEPENDENCY` | 中 | 应直接评估 `@mozilla/readability`、DOMPurify 等上游；Folo 包为 AGPL | 可选独立采用，补 SSRF 防护 |
| AI 聊天传输与本地会话模型 | `modules/ai-chat/store/transport.ts`、`services/index.ts` | `REWRITE_FROM_PATTERN` | 中 | `ai` SDK + Folo AGPL；服务端协议缺失 | 参考，不直接复用 |
| Action 条件树/Webhook UI | `store/modules/action`、desktop action UI | `DESIGN_REFERENCE` | 中 | 服务端执行器缺失；AGPL | 只参考规则编辑体验 |
| OPML 与 CLI 操作面 | `apps/cli`、discover import/export | `API_INTEGRATION`/`DESIGN_REFERENCE` | 低至中 | 绑定 Folo API；接口稳定性与条款未知 | 仅作迁移/可操作性参考 |
| Folo 远端 API/SDK | `api-client.ts`、`env.common.ts` | `API_INTEGRATION` | 高 | 外部服务、认证、可用性、数据边界未知 | 不作为核心依赖 |
| RSSHub 管理 UI | `queries/rsshub.ts`、`modules/rsshub` | `DESIGN_REFERENCE` | 低 | 后端代理缺失；AGPL | UI 参考，实际直接研究 RSSHub |
| MCP 连接管理 UI | `queries/mcp.ts` | `DESIGN_REFERENCE` | 中 | 秘密通过远端 API，代理未知 | 参考连接状态和权限提示 |
| `icons/mgc` | `icons/mgc/**`、根 `LICENSE` | `REJECT` | — | 明确禁止再分发 | 必须排除并全部替换 |

## 最值得保留的设计思想

1. 阅读器优先的信息密度、列表/条目/详情导航。
2. 远端同步与本地缓存分层，离线/响应性由客户端 DB 提升。
3. 将 OS 集成集中到 Electron main IPC 服务。
4. 搜索、摘要、翻译和聊天嵌入阅读上下文，而非独立工具页。
5. OPML、CLI、Webhook、MCP 等多种集成面在同一产品中可发现。

## 明确不复用

- 不直接 Fork 整仓：AGPL 网络交互义务、非再分发图标、后端缺失和安全整改成本叠加。
- 不采用现有 DB 作为长期情报库：它主动裁剪缓存且缺少证据字段。
- 不继承 BrowserWindow 的 `sandbox:false`、`contextIsolation:false`、`nodeIntegration:true`。
- 不把 Folo 远端 API 设为即时 AI 的唯一数据源或执行平面。
- 不从 Folo 源码抄取 `icons/mgc` 或生成的派生资产。

## 合规边界

任何直接复制 Folo AGPL 代码并分发、或通过网络提供修改版本的方案，都必须先完成 AGPL 义务分析和源码提供流程；图标禁再分发条款还需单独排除。本文不是法律意见，正式采用前必须法律复核。
