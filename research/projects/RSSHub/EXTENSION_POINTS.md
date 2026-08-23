# RSSHub 扩展点

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`）。

## 1. Route/Namespace 机制

这是最主要的 Provider/Adapter 机制。

- `lib/types.ts::Namespace`：来源名称、域名、分类、语言。
- `lib/types.ts::Route`：path、handler、示例、参数、features、radar、view。
- `lib/registry-helpers.ts::applyModulesToNamespaces()`：把模块合并到 registry。
- `scripts/workflow/build-routes.ts`：构建期生成 route registry、Radar rules、maintainers 和
  RoutePath 类型。

扩展方式：新增独立 namespace/route 文件，handler 返回 `Data`。这与“即时 AI”的来源
Adapter 思路高度一致，但直接复制/修改源码受 AGPL 约束，且上游要求应遵守其贡献规范。

## 2. npm 包 API

`lib/pkg.ts` 提供：

- `init(conf?)`
- `request(path)`
- `registerRoute(namespace, route, namespaceConfig?)`

它允许进程内注册自定义 Route。但它会把完整 RSSHub app/registry 引入同一进程，许可证与
依赖耦合明显高于 HTTP sidecar，因此当前不推荐作为默认集成方式。

## 3. HTTP Feed 与格式接口

任何 Route 都可通过 HTTP path 调用，并由 `template.tsx` 输出 RSS、Atom、JSON Feed 或
RSS3/UMS。对“即时 AI”而言这是最干净的 Adapter 边界：下游只解析标准 Feed，独立维护
持久化、去重和 AI。

## 4. 平台 API/OpenAPI

`lib/api/index.ts` 注册 Namespace、Radar、Category、Route Status、Follow Config 等元数据
API，并提供 `/api/openapi.json` 与 `/api/reference`。这些 API 主要用于发现和描述 Route，
不是历史新闻查询 API。

## 5. RSSHub Radar

`Route.radar` 描述网页 URL 到 RSSHub path 的映射；构建脚本生成 `radar-rules.json/js`。
这可用于来源发现，但依赖外部 Radar 客户端才能形成完整用户体验。

## 6. 缓存接口

`lib/utils/cache/base.ts::CacheModule` 约定 `init/get/has/set/status/clients`；当前实现有 memory、
Redis、HTTP、Worker KV。`tryGet()` 为 Route 详情抓取提供通用缓存。新增后端需要适配核心
初始化分支与 `globalCache.claim` 语义。

## 7. 抓取与浏览器适配

- `lib/utils/ofetch.ts`：标准 HTTP。
- `lib/utils/got.ts`：旧调用兼容层。
- `lib/utils/playwright.ts`：本地浏览器、远端 WebSocket/CDP 或 Worker browser binding。
- `lib/utils/proxy/**`：单/多代理、PAC 与 failover。

## 8. Webhook、CLI、MCP、数据库、自定义节点

| 扩展类型 | 静态结论 | 验证状态 |
|---|---|---|
| Webhook | 核心源码精确搜索未发现 webhook 入口 | `ABSENT_IN_CORE_SNAPSHOT` |
| CLI | 只有 package scripts，无业务 CLI 插件协议 | `SOURCE_VERIFIED` |
| MCP | 未发现平台 MCP server/client 实现 | `ABSENT_IN_CORE_SNAPSHOT` |
| 数据库接口 | 只有 cache 接口，无业务 repository/ORM | `ABSENT_IN_CORE_SNAPSHOT` |
| 自定义节点 | 没有 n8n 式节点系统；Route 即来源插件 | `SOURCE_VERIFIED` |
| 调度器 | 没有采集 scheduler；由外部请求触发 | `ABSENT_IN_CORE_SNAPSHOT` |

## 推荐扩展边界

```text
即时 AI SourceAdapter
  -> localhost RSSHub URL
  -> 标准 Feed 解析
  -> 自有 SourceRecord/ArticleRecord
```

需要新来源时优先顺序：先确认上游 RSSHub 是否已有 Route；其次在独立 RSSHub Fork/补丁或
向上游贡献；只有许可证和维护成本不合适时，才在“即时 AI”实现自己的小型 Adapter。
