# Folo 静态研究摘要

> 统一证据标识：`RSSNext/Folo`，默认分支 `dev`，固定提交 `7c220c69a841defbfeeb00a86ed75ad482b22a57`，下载日期 `2026-08-23`。研究原件为 `upstream/Folo-snapshot` 的 `OFFICIAL_ARCHIVE_SNAPSHOT`，归档 SHA-256 为 `EA6661B150339412D665E9ECEE45A3CF7E25D0BA87B8DB3621621FED30CBE9AB`。目录内无 `.git`，因此不能算完成 Git 克隆，也无法用本地 Git 历史复核分支、提交或活跃度。

## 结论

Folo 是一个以订阅流、列表、收件箱和阅读器为核心的多端信息阅读客户端。该快照提供成熟的 Electron/React 桌面界面、Expo 移动端、SSR 分享层、CLI、本地缓存和多种 AI/自动化交互，但抓取、RSS 解析、去重、规则执行、AI 任务执行等核心服务端实现并不在仓库中；客户端主要通过 `@follow-app/client-sdk` 访问 `https://api.folo.is`。

它与“即时 AI”的阅读工作台、订阅管理、全文阅读、搜索、摘要、翻译和通知需求重合较多，但与可审计的财经采集、实体识别、事件分类、证据原文长期保存、数据源本地自治重合较少。推荐角色是 `UI_REFERENCE`，不推荐作为主底座或直接代码 Fork。

## 目标用户与核心功能

- 目标用户：需要聚合 RSS、列表和收件箱信息并进行跨端阅读的个人用户。
- 核心功能：订阅与 OPML、时间线/列表、全文阅读、收藏与已读状态、本地模糊搜索、AI 摘要/翻译/聊天、动作规则、推送、RSSHub 管理、MCP 连接和桌面集成。
- 产品边界：仓库是“客户端 + 本地缓存 + 分享/OTA 辅助服务”，不是可自托管的完整内容采集后端。

## 与即时 AI 的重合度

按需求维度静态估计约 `55%`：阅读界面和用户工作流高度重合；数据采集、可追溯证据库、财经结构化、稳定去重和本地长期保存缺口显著。该比例是架构判断，不是运行测量。

## 最强的五项能力

1. Electron/React 桌面阅读体验及移动端共用设计。
2. Jotai、Zustand、TanStack Query、Drizzle/SQLite 组成的客户端状态与缓存链。
3. 订阅、列表、收件箱、搜索、收藏、全文阅读和 OPML 的完整客户端工作流。
4. AI 摘要、翻译、聊天、定时任务和 MCP 的丰富交互入口。
5. Windows Squirrel/AppX、自动更新、托盘、深链、通知和系统集成的工程化构建链。

## 最大的五项问题

1. 核心业务后端缺失，无法从仓库验证抓取、解析、去重、AI 执行和任务调度算法。
2. 根许可证为 AGPL-3.0，且 `icons/mgc` 被明确禁止再分发，直接 Fork/发布存在重大合规障碍。
3. Electron 窗口关闭 sandbox/context isolation 并启用 Node integration 与 webview，安全基线不适合直接继承。
4. 本地数据库是会裁剪旧条目的工作缓存，不是不可变、可核验的长期证据库，也不支持 `H:\即时AI文件库`。
5. 客户端强依赖 Folo 远端 API；自托管闭环、离线能力和远端数据处理边界无法从快照确认。

## 关键源码证据

| 项目 | 提交 | 源码路径 | 类/函数/配置 | 结论 | 状态 |
|---|---|---|---|---|---|
| RSSNext/Folo | `7c220c6` | `apps/desktop/layer/renderer/src/lib/api-client.ts` | `FollowClient` | 桌面客户端通过外部 SDK 访问配置的远端 API。 | `SOURCE_VERIFIED` |
| RSSNext/Folo | `7c220c6` | `packages/internal/shared/src/env.common.ts` | `PROD.apiUrl` | 生产 API 默认值为 `https://api.folo.is`。 | `SOURCE_VERIFIED` |
| RSSNext/Folo | `7c220c6` | `packages/internal/store/src/modules/entry/store.ts` | `EntrySyncServices.fetchEntries` | 条目从远端 entries/inbox API 拉取后写入客户端状态与本地库。 | `SOURCE_VERIFIED` |
| RSSNext/Folo | `7c220c6` | `packages/internal/database/src/db.desktop.ts` | `IDBMirrorVFS`、`follow.db` | 桌面数据是 WA-SQLite 映射到 IndexedDB 的本地缓存。 | `SOURCE_VERIFIED` |
| RSSNext/Folo | `7c220c6` | `LICENSE` | 末尾附加条款 | `icons/mgc` 内容不可再分发。 | `SOURCE_VERIFIED` |

README 中关于可下载 Windows 成品、项目活跃状态和完整功能的宣传属于 `DOC_ONLY`：**仅在文档中发现，尚未通过源码运行验证。**
