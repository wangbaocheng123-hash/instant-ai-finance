# RSSHub 源码架构

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`）。

## 总览

RSSHub 是 TypeScript/Node.js 的 Hono Web 服务。Node 入口把 Hono `app.fetch` 交给
`@hono/node-server`；应用中间件包围路由注册器；Route handler 抓取并返回统一对象；
出站阶段再完成缓存、参数后处理、响应头与 Feed 渲染。

### 文字架构图

```text
RSS/HTTP 客户端
  -> Node/Worker/Container/Vercel 入口
  -> Hono app-bootstrap 中间件栈
  -> registry 路由注册与惰性模块加载
  -> lib/routes/<namespace>/<route>.ts[x]
  -> ofetch/got/Playwright/第三方 SDK
  -> 外部网站、开放 API、需凭据 API
  <- Route 返回 Data/DataItem
  <- route cache + 参数处理 + anti-hotlink + header
  <- template 渲染 RSS / Atom / JSON Feed / RSS3
  <- HTTP 响应

旁路：memory / Redis / HTTP cache / Worker KV
旁路：/api 元数据与 OpenAPI、/healthz、/metrics
旁路：Winston 日志、Sentry/Honeybadger、OpenTelemetry
```

### Mermaid 架构图

```mermaid
flowchart LR
    C[RSS 客户端或即时 AI] --> E{运行入口}
    E -->|Node| I[lib/index.ts]
    E -->|Vercel| S[lib/server.ts]
    E -->|Worker| W[lib/worker.ts]
    E -->|Container Worker| CW[lib/container.ts]
    I --> A[Hono app-bootstrap]
    S --> A
    W --> AW[app.worker]
    CW --> I
    A --> M[中间件栈]
    AW --> M
    M --> R[registry]
    R --> H[Route handler]
    H --> F[ofetch / got / Playwright / SDK]
    F --> U[外部网站与 API]
    H --> D[Data / DataItem]
    D --> P[parameter / anti-hotlink / header]
    P --> T[template]
    T --> O[RSS / Atom / JSON Feed / RSS3]
    M <--> K[(memory / Redis / HTTP / KV cache)]
    A --> API[/api + OpenAPI]
    A --> OBS[healthz / metrics / logs / tracing]
```

## 组件与源码证据

| 层 | 源码/标识符 | 调用关系与结论 | 状态 |
|---|---|---|---|
| Node 入口 | `lib/index.ts`，`serve()`、`config.enableCluster` | 监听默认 1200，可单进程或按 CPU cluster | `SOURCE_VERIFIED` |
| 应用装配 | `lib/app-bootstrap.tsx`，`new Hono()`、`app.use()` | 注册压缩、日志、追踪、访问控制、缓存、参数、渲染等 | `SOURCE_VERIFIED` |
| 路由注册 | `lib/registry.ts`，`registerRssRoutes()` | 生产加载构建产物，开发使用惰性 registry | `SOURCE_VERIFIED` |
| 模块编目 | `lib/registry-helpers.ts`，`applyModulesToNamespaces()` | 将 namespace、route、apiRoute 合并为统一 registry | `SOURCE_VERIFIED` |
| 构建期发现 | `scripts/workflow/build-routes.ts` | 生成 routes、radar、maintainers 和 RoutePath 构建产物 | `SOURCE_VERIFIED` |
| 统一模型 | `lib/types.ts`，`Route`、`Data`、`DataItem` | 规定路由元数据和 Feed 输出字段 | `SOURCE_VERIFIED` |
| 抓取层 | `lib/utils/ofetch.ts`、`lib/utils/got.ts` | 统一重试、代理偏好和 got 兼容封装 | `SOURCE_VERIFIED` |
| 浏览器层 | `lib/utils/playwright.ts` | 为强反爬路由提供本地或远程浏览器能力 | `SOURCE_VERIFIED` |
| 缓存层 | `lib/utils/cache/index.ts`、`middleware/cache.ts` | 内容缓存、整条路由缓存、并发请求 claim | `SOURCE_VERIFIED` |
| 后处理 | `lib/middleware/parameter.ts`，`middleware()` | 规范化、排序、筛选、限量、全文与 OpenAI 可选处理 | `SOURCE_VERIFIED` |
| 输出层 | `lib/middleware/template.tsx` | RSS、Atom、JSON Feed、RSS3/UMS 渲染 | `SOURCE_VERIFIED` |
| API | `lib/api/index.ts`，`OpenAPIHono` | namespace、radar、category、route status、Follow 配置元数据 | `SOURCE_VERIFIED` |
| 包模式 | `lib/pkg.ts`，`init/request/registerRoute` | 可作为 npm 包发起内部请求和注册自定义 route | `SOURCE_VERIFIED` |

## 前端、后端与调度

- 前端：只有欢迎页、错误页、API reference 和 XML/JSON 输出视图，不是阅读器 UI。
- 后端：Hono + Node/Worker，核心是动态 Route handler。
- 调度器：核心源码精确搜索未发现 `cron`、`scheduler`；入口也没有周期抓取循环。代理健康检查
  的 `setInterval` 不是信息采集调度器。结论：必须由 Feed 客户端或外部工作流定时轮询。
- 数据源：`lib/routes/**`，来源可以是 API、HTML、现成 RSS、浏览器渲染页和第三方 SDK。
- 存储：只包含缓存、日志与 Docker Redis volume；没有正式业务数据库或新闻表。
- AI：`parameter.ts` 可在 `?chatgpt=` 且配置 API key 时调用兼容 OpenAI Chat Completions。
- 通知：`ViewType.Notifications` 只是视图元数据；没有主动消息投递模块。
- MCP：未发现 RSSHub MCP 服务实现。唯一精确 `mcp` 命中是某个具体来源路由内容，不是平台能力。

## Worker 差异

`lib/app.worker.tsx` 明确排除 Honeybadger、Sentry、antiHotlink、parameter 等重中间件，
并且不暴露 API routes；因此 Worker 形态与 Node 完整形态能力不完全等价。Worker 通过
`lib/utils/cache/index.worker.ts` 与 KV 缓存，并可绑定 Browser Rendering API。

## 外部依赖边界

`package.json` 锁定 Hono、Cheerio、ofetch、undici、Patchright/Playwright 兼容层、Redis、
Winston、Sentry/Honeybadger、OpenTelemetry 以及多个来源 SDK。路由对上游站点和凭据的
依赖远多于核心框架依赖；是否可用必须逐路由验证，不能由服务成功启动推定。
