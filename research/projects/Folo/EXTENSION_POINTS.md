# 扩展点

> 证据基线：`RSSNext/Folo@7c220c69a841defbfeeb00a86ed75ad482b22a57`；`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`；未运行。

## 可确认的扩展接缝

| 接缝 | 源码路径/符号 | 能力 | 限制 | 状态 |
|---|---|---|---|---|
| 远端 API SDK | `apps/desktop/layer/renderer/src/lib/api-client.ts::FollowClient` | subscriptions、entries、AI、action 等客户端 API | 后端和协议治理在仓库外，强平台依赖 | `SOURCE_VERIFIED` |
| Electron IPC | `apps/desktop/layer/main/src/ipc/index.ts` | App/Auth/CLI/Menu/Reader/Setting/Integration 等服务 | 内部 IPC，不是稳定第三方 ABI | `SOURCE_VERIFIED` |
| Renderer bridge | `apps/desktop/layer/renderer/src/providers/extension-expose-provider.tsx`、`packages/internal/shared/src/bridge.ts` | renderer 向 main/webview 暴露上下文 | 名称虽有 extension，但本质是内部桥接 | `SOURCE_VERIFIED` |
| CLI | `apps/cli/src/index.ts` | 订阅、时间线、搜索、OPML 等命令 | 仍依赖远端 Folo API | `SOURCE_VERIFIED` |
| Action/Webhook | `packages/internal/store/src/modules/action/store.ts` | 嵌套规则、通知、Webhook、rewrite 配置 | 服务端执行器缺失 | `SOURCE_VERIFIED`（调用端） |
| MCP | `apps/desktop/layer/renderer/src/queries/mcp.ts` | 添加 SSE/streamable-http 连接并管理工具 | 远端代理实现缺失，连接秘密处理不透明 | `SOURCE_VERIFIED`（调用端） |
| RSSHub | `apps/desktop/layer/renderer/src/queries/rsshub.ts` | 管理/使用 RSSHub 实例 | 通过 Folo API 间接管理，不是本地数据源适配器 | `SOURCE_VERIFIED`（调用端） |
| OPML | desktop discover/import 与 CLI | 订阅迁移 | 只覆盖 Feed 清单，不覆盖证据和规则 | `SOURCE_VERIFIED` |
| Integration | `apps/desktop/layer/renderer/src/modules/integration`、main `IntegrationService` | Obsidian、Eagle、qBittorrent、custom fetch、URL scheme | 任意请求能力扩大 SSRF/凭据风险 | `SOURCE_VERIFIED` |
| Deep links | main protocol manager | `follow://`/`folo://` 导航与认证 | 需严格校验所有外部输入 | `SOURCE_VERIFIED` |

## 不是产品插件系统的目录

- 根 `plugins/` 是构建和 lint 插件。
- 编辑器中的若干 `plugins` 是 Lexical 内部组件。
- `ExtensionExposeProvider` 是跨上下文方法暴露层。

未发现运行时发现、安装、隔离、版本协商、权限声明和卸载第三方“数据源/处理器/通知器”的完整机制。因此 Folo 不具备可直接复用为即时 AI 采集插件平台的扩展体系。

## 可借鉴的接口形态

1. 将远端数据访问集中在 typed client 和 sync service。
2. 用领域 morph 层隔离 API DTO 与本地模型。
3. 将桌面 OS 能力封装为 IPC 服务。
4. 对外提供 CLI/OPML/Webhook 等松耦合集成面。

这些是设计参考，不是复制授权。若即时 AI 建立插件系统，应另行定义数据源、规范化器、实体识别、评分器、通知器的 manifest、权限、隔离、版本和审计日志。

## 未验证项

服务端 action 执行、RSSHub 代理、MCP 工具调用、API 速率限制和第三方集成授权均不在快照中，状态为 `UNVERIFIED`。README/界面文字若声称支持某连接器，只能视为 `DOC_ONLY`：**仅在文档中发现，尚未通过源码验证。**
