# 入口点

> 项目：`RSSNext/Folo`；提交：`7c220c69a841defbfeeb00a86ed75ad482b22a57`；快照：`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

## 桌面入口

| 源码路径 | 入口/配置 | 作用 | 状态 |
|---|---|---|---|
| `apps/desktop/package.json` | `main: ./dist/main/index.js` | Electron 打包后的主进程入口。 | `SOURCE_VERIFIED` |
| `apps/desktop/layer/main/src/index.ts` | 顶层 import | 加载前置配置与 bootstrap。 | `SOURCE_VERIFIED` |
| `apps/desktop/layer/main/src/before-bootstrap.ts` | `protocol.registerSchemesAsPrivileged` | 注册 `app://`，并设置开发 userData 目录。 | `SOURCE_VERIFIED` |
| `apps/desktop/layer/main/src/bootstrap.ts` | `BootstrapManager.start()` | 启动 Electron 生命周期。 | `SOURCE_VERIFIED` |
| `apps/desktop/layer/main/preload/index.ts` | `electronAPI`、clipboard 暴露 | preload 与 renderer 桥。 | `SOURCE_VERIFIED` |
| `apps/desktop/layer/renderer/src/main.tsx` | React `createRoot`、`RouterProvider` | renderer 入口。 | `SOURCE_VERIFIED` |
| `apps/desktop/layer/renderer/src/App.tsx` | `RootProviders`、`Outlet` | UI 根组件。 | `SOURCE_VERIFIED` |
| `apps/desktop/layer/renderer/src/router.tsx` | hash/browser router | Electron 使用 hash 路由，Web 使用 browser 路由。 | `SOURCE_VERIFIED` |

页面路由由 `apps/desktop/layer/renderer/src/pages/**/*.tsx` 和 `vite-plugin-route-builder` 生成，不是手写中央路由表。

## API 和后台辅助入口

| 源码路径 | 入口/配置 | 作用 | 状态 |
|---|---|---|---|
| `apps/desktop/layer/renderer/src/lib/api-client.ts` | `FollowClient` | renderer 的远端业务 API 客户端。 | `SOURCE_VERIFIED` |
| `apps/ssr/index.ts` | `createApp()` | Fastify 分享/元信息服务，端口 2234。 | `SOURCE_VERIFIED` |
| `apps/ssr/worker-entry.ts` | Hono worker | Cloudflare Worker 分享与 OG 入口。 | `SOURCE_VERIFIED` |
| `apps/ota/src/index.ts` | Hono app、scheduled handler | 客户端发布清单和版本同步，不是内容抓取器。 | `SOURCE_VERIFIED` |
| `api/vercel_webhook.ts` | 默认 handler | 验证 Vercel webhook 并清 Cloudflare cache。 | `SOURCE_VERIFIED` |

## CLI 入口

`apps/cli/src/index.ts` 使用 Commander 注册 `auth`、`timeline`、`subscription`、`entry`、`feed`、`list`、`search`、`collection`、`opml` 和 `unread` 命令；`apps/cli/src/client.ts` 同样访问远端 Folo API。它是 API 客户端，不是独立采集引擎。

## 移动端入口

`apps/mobile` 是 Expo/React Native 应用，`apps/mobile/package.json` 版本为 `0.5.8`。本轮面向 Windows 桌面，只确认其存在及共享 packages 使用方式，未对 iOS/Android 原生启动链做深度运行验证。

## 可执行命令入口

- `apps/desktop/package.json`: `dev:web`、`dev:electron`、`build:electron-vite`、`build:electron-forge`、`build:electron-forge:ms`。
- 根 `package.json`: `dev:web`、`build`、`test`、`typecheck`。
- `pnpm-workspace.yaml`: monorepo workspace 边界。

以上命令只由源码清单确认，均未在本机执行，不能标记为 `RUNTIME_VERIFIED`。
