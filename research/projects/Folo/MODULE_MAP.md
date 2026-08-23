# 模块地图

> 源码基线：`RSSNext/Folo@7c220c69a841defbfeeb00a86ed75ad482b22a57`；`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

## 应用层

| 模块 | 路径 | 责任 | 对即时 AI 的意义 |
|---|---|---|---|
| Desktop renderer | `apps/desktop/layer/renderer/src` | React 页面、阅读器、订阅、AI、设置、搜索 | 主要 UI 参考 |
| Electron main/preload | `apps/desktop/layer/main` | 窗口、IPC、协议、推送、更新、托盘、集成 | 桌面壳参考，安全配置不可照搬 |
| Mobile | `apps/mobile` | Expo/React Native 客户端 | 后期移动端设计参考 |
| SSR | `apps/ssr` | 分享页面、meta、OG | 非核心业务后端 |
| OTA | `apps/ota` | 版本清单、更新策略、商店/GitHub 发布同步 | 桌面/移动发布基础设施参考 |
| CLI | `apps/cli` | 远端 API 命令行客户端 | 自动化接口和操作模型参考 |
| API webhook | `api/vercel_webhook.ts` | 部署缓存清理 | 与情报采集无关 |

## 核心共享包

| 模块 | 路径 | 关键内容 | 验证状态 |
|---|---|---|---|
| Store | `packages/internal/store/src/modules` | entry/feed/subscription/action/summary/translation 等 Zustand store 与 sync service | `SOURCE_VERIFIED` |
| Database | `packages/internal/database` | Drizzle schemas、WA-SQLite/Expo SQLite、migrations、services | `SOURCE_VERIFIED` |
| Atoms | `packages/internal/atoms` | Jotai 设置与持久化 | `SOURCE_VERIFIED` |
| Shared env | `packages/internal/shared/src/env.*` | API、Web、OTA 环境地址 | `SOURCE_VERIFIED` |
| Readability | `packages/readability` | URL 获取、字符集识别、清洗、Mozilla Readability | `SOURCE_VERIFIED` |
| UI | `packages/ui` 及 desktop components/modules | 通用组件和产品 UI | `SOURCE_VERIFIED` |
| Constants/types | `packages/*` | 领域常量、类型、hooks、utils | `SOURCE_VERIFIED` |

## 桌面功能模块

- `apps/desktop/layer/renderer/src/modules/entry-content`：条目正文和阅读视图。
- `apps/desktop/layer/renderer/src/modules/ai-chat`：聊天 UI、流式传输、本地会话持久化。
- `apps/desktop/layer/renderer/src/modules/ai-task`：AI 定时任务配置及远端 API 查询。
- `apps/desktop/layer/renderer/src/modules/rsshub`：RSSHub 实例管理 UI。
- `apps/desktop/layer/renderer/src/modules/integration`：Obsidian、Eagle、qBittorrent、自定义 fetch/URL scheme 等。
- `apps/desktop/layer/renderer/src/modules/settings`：账户、AI、通知、外观和集成配置。
- `apps/desktop/layer/renderer/src/store/search`：本地 Fuse 索引和查询。
- `apps/desktop/layer/renderer/src/queries`：AI config、MCP、RSSHub 等远端查询。

## 插件目录辨析

根 `plugins/` 主要是 monorepo 的构建/ESLint 插件；Lexical 的若干 `plugins` 是编辑器内部组件。两者都不是允许第三方动态安装数据源、分类器或处理节点的产品插件系统。`ExtensionExposeProvider` 是 renderer 与 Electron/webview 的桥接暴露层，也不是稳定的外部插件 ABI。

## 缺失模块

快照未包含可定位的生产级 RSS crawler、刷新调度、内容规范化/跨源去重、AI 服务端、动作执行器、MCP 服务端代理或财经实体/事件处理模块。这些不能因为 UI 和 SDK 类型存在就判定为开源实现。
