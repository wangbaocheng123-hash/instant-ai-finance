# 数据流

> 项目：`RSSNext/Folo@7c220c69a841defbfeeb00a86ed75ad482b22a57`；静态分析；归档快照无 `.git`。

## 订阅到阅读主链

```text
用户输入 Feed / OPML
  → SubscriptionSyncService.subscribe
  → api().subscriptions.create（远端 Folo API）
  → feed/subscription/unread store + 本地 SQLite
  → EntrySyncServices.fetchEntries
  → api().entries.list 或 api().inbox.list
  → apiMorph.toEntryList
  → entryActions.upsertMany
  → EntryService 写入本地 DB
  → React 时间线/阅读器展示
```

### 证据

| 源码路径 | 类/函数/配置 | 调用关系或上下文 | 结论 | 状态 |
|---|---|---|---|---|
| `packages/internal/store/src/modules/subscription/store.ts` | `SubscriptionSyncService.subscribe` | `subscriptions.create` 后 upsert 本地领域状态 | 订阅创建以远端 API 为权威。 | `SOURCE_VERIFIED` |
| `packages/internal/store/src/modules/entry/store.ts` | `EntrySyncServices.fetchEntries` | entries/inbox API → morph → store/DB | 条目由远端服务提供。 | `SOURCE_VERIFIED` |
| `packages/internal/store/src/morph/api.ts` | `apiMorph.toEntryList` | DTO 转 `EntryModel` | 规范化发生在客户端 DTO 映射层，但抓取/解析在仓库外。 | `SOURCE_VERIFIED` |
| `packages/internal/database/src/services/entry.ts` | `EntryService.upsertMany` | 以条目 ID 冲突更新 | 本地只按 `id` 做 upsert。 | `SOURCE_VERIFIED` |

## 全文阅读链

`EntrySyncServices.fetchEntryReadabilityContent` 优先请求远端 readability 内容；需要本地回退时，通过 Electron IPC 调用 `ReaderService.readability`。`packages/readability/src/index.ts::readability` 获取目标 URL，检测字符集，使用 JSDOM、DOMPurify 和 Mozilla Readability 解析；`sanitize.ts` 清洗内容并只保留受限的 YouTube HTTPS iframe。

这一链可作为阅读器模式参考，但主进程网络请求没有可见的私网/IP/scheme 限制，不能直接复用到安全要求更高的财经采集器。

## AI 数据流

- 摘要：`SummarySyncService.generateSummary` → `api().ai.summary` → summaries 表。
- 翻译：`TranslationSyncService.generateTranslation`/batcher → `api().ai.translationBatch` NDJSON → translations 表。
- 聊天：`createChatTransport`/`ExtendChatTransport` → `${VITE_API_URL}/ai/chat` 流式响应 → 本地 AI 会话/消息表。
- 定时任务：AI Task Query → `followApi.aiTask.*`；客户端支持 once/daily/weekly/monthly 配置。
- MCP：`queries/mcp.ts` → `followApi.mcp.*`，管理 streamable-http/SSE 连接及工具。

AI 模型调用、服务端 Prompt、任务调度和 MCP 代理实现均不在快照中，属于 `UNVERIFIED`。不能把“存在客户端调用”写成“已验证 AI 后端能力”。

## RSS 与 RSSHub 数据流

订阅 URL、OPML 导入导出和 RSSHub 管理均通过远端 Folo API。`queries/rsshub.ts` 能创建、使用、删除和查询 RSSHub 实例元数据，但快照未包含 Feed 抓取调度器或通用 RSS 解析服务端。核心抓取与刷新逻辑 `UNVERIFIED`。

## 动作、通知与自动化

`ActionSyncService.fetchRules/saveRules` 管理嵌套条件、通知、Webhook 和 rewrite 结果，但执行引擎不在仓库中。renderer 仅在规则包含新条目通知时注册 Web Push；Electron main 使用 push receiver 展示系统通知并导航条目。AI Task UI 源码提示当前通知通道只有 email。

## 搜索与去重

- `apps/desktop/layer/renderer/src/store/search/index.ts::SearchActions.createLocalDbSearch` 从本地 entries/feeds/subscriptions 建立 Fuse 索引。
- 条目表以 `id` 为主键，写入时按 ID 冲突 upsert；`guid` 存储但不是唯一约束。
- 未找到内容哈希、规范 URL、跨源相似度或证据级去重实现。服务端是否去重及其算法 `UNVERIFIED`。

## 财经证据链缺口

当前数据流没有原始 HTTP 响应归档、采集批次、内容哈希、不可变时间戳、公司/商品实体、事件表和来源证据版本。因此不能直接满足即时 AI 的长期投资情报数据库要求。
