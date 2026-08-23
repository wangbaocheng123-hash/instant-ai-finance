# 架构

> 证据范围：`RSSNext/Folo@7c220c69a841defbfeeb00a86ed75ad482b22a57`，`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`；本报告只做静态分析。

## 总体结构

```text
Electron main / preload
        │ IPC、窗口、协议、通知、更新、系统集成
        ▼
React renderer ── Jotai / Zustand / TanStack Query
        │                         │
        │ @follow-app/client-sdk │ Drizzle service
        ▼                         ▼
api.folo.is（仓库外）       WA-SQLite + IndexedDB
        │
        ├─ subscriptions / entries / actions / RSSHub
        ├─ AI summary / translation / chat / tasks / MCP
        └─ 服务端抓取、解析、调度、去重实现：快照中缺失
```

同一 monorepo 还包含 Expo 移动端、Fastify/Cloudflare Worker 分享 SSR、OTA Worker、CLI 和共享 packages。`apps/ssr` 只负责分享页、元信息和 OG 等，不是核心业务后端；根 `api/vercel_webhook.ts` 只负责部署缓存清理。

## 桌面分层

1. `apps/desktop/layer/main/src/index.ts` 先加载 `before-bootstrap`，再执行 `bootstrap.ts`。
2. `BootstrapManager.start()` 初始化 `AppManager`、单实例锁、协议、Cookie/响应头、主窗口和认证深链。
3. `AppManager.onReady()` 注册 IPC、协议、缓存清理、菜单、推送、代理、更新器、托盘和 CLI token 同步。
4. `WindowManager.createMainWindow()` 创建 BrowserWindow，并按开发、远端热更新或本地构建三种来源加载 renderer。
5. renderer 的 `src/main.tsx` 注入 API、认证、Query 等上下文，调用 `initializeApp()`，再交给 React Router。
6. `initializeApp()` 执行数据库 hydration、设置/i18n/分析初始化、AI 会话恢复和用户/服务端设置同步。

## 状态与数据分层

- Jotai：可持久化用户设置和细粒度 UI 状态。
- Zustand：feeds、entries、subscriptions、unread、users 等领域 store。
- TanStack Query：远端查询缓存，选择性持久化到 `localStorage`。
- Drizzle service：为 WA-SQLite/IndexedDB 提供本地表访问。
- Electron Store：主进程窗口、代理、推送和集成配置。

本地状态不是数据源权威：创建订阅、拉取条目、生成摘要、执行翻译和管理动作规则都先调用远端 API，再把结果映射到本地 store/DB。

## 后端边界

仓库中未发现实现以下能力的完整服务端：RSS 拉取调度、Feed 解析、跨源规范化/去重、AI 模型调用执行、动作规则执行、定时任务执行、MCP 服务端代理。能够定位的是客户端调用点。服务端内部算法与部署方式均为 `UNVERIFIED`，不能据客户端接口反推其实现。

## 关键证据

| 源码路径 | 类/函数/配置 | 调用关系或上下文 | 结论 | 状态 |
|---|---|---|---|---|
| `apps/desktop/layer/main/src/manager/bootstrap.ts` | `BootstrapManager.start`、`registerAppEvents` | Electron 进程启动链 | 桌面主进程总引导器。 | `SOURCE_VERIFIED` |
| `apps/desktop/layer/main/src/manager/app.ts` | `AppManager.init`、`onReady` | IPC、协议、通知、更新、托盘 | 系统能力集中在 Electron main。 | `SOURCE_VERIFIED` |
| `apps/desktop/layer/main/src/manager/window.ts` | `WindowManager.createMainWindow` | 创建 BrowserWindow 并装载 renderer | 桌面 UI 采用 Web renderer。 | `SOURCE_VERIFIED` |
| `apps/desktop/layer/renderer/src/initialize/index.ts` | `initializeApp` | DB hydration → 设置 → AI 会话 → 服务端同步 | renderer 启动后的数据初始化顺序。 | `SOURCE_VERIFIED` |
| `apps/desktop/layer/renderer/src/providers/root-providers.tsx` | `RootProviders` | Jotai、Query、配置与命令 Provider | React 应用组合根。 | `SOURCE_VERIFIED` |
| `apps/ssr/index.ts` | `createApp` | Fastify 分享/元信息服务 | SSR 不是核心 Folo API。 | `SOURCE_VERIFIED` |

## 架构适配判断

可借鉴“桌面壳 + Web UI + 本地缓存”的交互架构，但不应照搬其安全配置、远端 API 耦合或缓存数据模型。即时 AI 需要另建有清晰证据链的数据层，并将正式业务文件固定在 `H:\即时AI文件库`；研究阶段不能因此锁定 Electron。
