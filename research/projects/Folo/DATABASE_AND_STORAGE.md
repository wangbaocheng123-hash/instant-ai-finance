# 数据库与存储

> 基线：`RSSNext/Folo@7c220c69a841defbfeeb00a86ed75ad482b22a57`；只读静态分析；无 `.git`。

## 桌面数据库

`packages/internal/database/src/db.desktop.ts` 使用 Drizzle SQLite proxy、`wa-sqlite` 和 `IDBMirrorVFS`。逻辑数据库名为 `follow.db`，VFS 镜像在 IndexedDB `WA_SQLITE` 中。移动端 `db.rn.ts` 使用 Expo SQLite，同名数据库。

主要表位于 `packages/internal/database/src/schemas/index.ts`：

- feeds、subscriptions、inboxes、lists、unread、users；
- entries、collections、summaries、translations、images；
- ai_chat_sessions、ai_chat_messages。

entries 保存标题、URL、GUID、正文/描述、可读性内容、发布时间、feedId 和已读等客户端字段；未看到原始 HTTP 响应、采集批次、原文哈希、证据版本、公司/商品实体或事件模型。

## 缓存保留策略

`packages/internal/database/src/services/entry.ts::EntryService.getEntriesToHydrate` 按发布时间载入条目，但对每个订阅来源只保留最多 20 条 hydration 数据并清理超额 ID。翻译 hydration 还会清理超过 7 天的数据。这说明该库是客户端工作缓存，不是长期、不可变的证据库。

entries 以 `id` 为主键，`upsertMany` 只按冲突 ID 更新；`guid` 没有唯一约束，未找到基于内容哈希或规范 URL 的本地去重。

## 其他持久化

| 位置/机制 | 源码证据 | 内容 | 风险 |
|---|---|---|---|
| `localStorage` | `apps/desktop/layer/renderer/src/lib/query-client.ts` | 选择性 Query cache，最大约 7 天 | 非加密、不可作为证据库 |
| Jotai storage | `packages/internal/atoms/src/helper/setting.ts::createSettingAtom` | `atomWithStorage(getStorageNS(...))` 保存设置 | 默认浏览器存储，可能包含 BYOK/集成敏感值 |
| Electron Store | `apps/desktop/layer/main/src/lib/store.ts` | `new Store({name: "db"})` 保存窗口、代理、推送、集成状态等 | 位于 Electron userData，非业务库抽象 |
| Cookies/Cache/SW | Electron session/userData | 登录和 Web 运行数据 | 可被 clearAllData 清除 |
| TTS Cache | main 进程 TTS 代码 | `userData/Cache/tts` | 缓存位置不可配置到 H 盘 |
| CLI config | `apps/cli/src/config.ts` | Windows 下 `%USERPROFILE%\.folo\config.json` | token 明文落盘 |

## 导出与清理

`db.desktop.ts` 提供数据库导出 Blob 和 `deleteDB`；Electron 设置服务可清 Cookies、IndexedDB、LocalStorage、Service Worker 等客户端数据。它们是用户端维护能力，不构成版本化证据导出协议。

## 与 H 盘要求的冲突

源码中没有 `H:\即时AI文件库` 或可将所有业务数据、证据原文、数据库、备份、缓存和日志统一重定向至指定库的抽象。Electron userData、IndexedDB、localStorage、CLI homedir 配置分散在系统用户目录。直接采用会违反即时 AI 的数据位置约束。

## 复用判断

可把“领域 store → service → SQLite cache hydration”作为模式参考；不得把 Folo 本地库误当成正式情报库。即时 AI 需要独立设计追加式证据表、原文/附件文件布局、内容哈希、采集时间、来源版本、实体/事件索引和备份恢复，并把正式根目录参数化为 `H:\即时AI文件库`。
