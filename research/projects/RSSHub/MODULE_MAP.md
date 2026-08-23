# RSSHub 核心模块图

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`）。

| 模块 | 作用与入口 | 主要依赖 | 独立性/复用难度 | 许可证影响 | 证据状态 |
|---|---|---|---|---|---|
| `lib/index.ts` | Node 监听、cluster；入口 `serve()` | Hono Node server、config | 高耦合于应用；中 | AGPL | `SOURCE_VERIFIED` |
| `lib/app-bootstrap.tsx` | 完整 Hono 装配 | middleware、registry、api | 应用核心；高 | AGPL | `SOURCE_VERIFIED` |
| `lib/registry*.ts` | Route 发现、排序、注册、惰性加载 | Hono、route metadata | 模式可借鉴；直接抽取中高 | AGPL | `SOURCE_VERIFIED` |
| `scripts/workflow/build-routes.ts` | 生成 registry/radar/maintainer/route path | tldts、tosource、源码扫描 | 构建工具；中 | AGPL | `SOURCE_VERIFIED` |
| `lib/types.ts` | `Data`/`DataItem`/`Route`/`Namespace` 合同 | Hono 类型 | 设计高度可借鉴；直接复制需谨慎 | AGPL | `SOURCE_VERIFIED` |
| `lib/routes/**` | 数千来源文件组成的适配器层 | ofetch/got/cache/Playwright/SDK | 单路由相对隔离；来源维护成本高 | AGPL + 来源条款 | `SOURCE_VERIFIED` |
| `lib/utils/ofetch.ts` | fetch 重试与代理切换提示 | ofetch、config | 可替换；中 | AGPL | `SOURCE_VERIFIED` |
| `lib/utils/got.ts` | 兼容旧 got 调用的适配层 | ofetch、destr | 内部迁移桥；低独立性 | AGPL | `SOURCE_VERIFIED` |
| `lib/utils/request-rewriter/**` | 全局 fetch/http(s) UA、代理、限速重写 | undici、proxy、rate limiter | 全局副作用强；高 | AGPL | `SOURCE_VERIFIED` |
| `lib/utils/playwright*` | 浏览器抓取、本地/远程绑定 | patchright/Worker browser | 可选但环境成本高；中高 | AGPL + 浏览器依赖 | `SOURCE_VERIFIED` |
| `lib/utils/cache/**` | memory/Redis/HTTP/KV 缓存与 `tryGet` | LRU、ioredis、fetch/KV | 接口清晰；中 | AGPL | `SOURCE_VERIFIED` |
| `lib/middleware/cache.ts` | 整路由缓存与并发 claim | xxhash、cache module | Hono 相关；中 | AGPL | `SOURCE_VERIFIED` |
| `lib/middleware/parameter.ts` | Feed 规范化、过滤、全文、OpenAI | Cheerio、RE2JS、Mercury、OpenAI API | 功能密集且耦合；高 | AGPL | `SOURCE_VERIFIED` |
| `lib/middleware/template.tsx` | RSS/Atom/JSON/RSS3 输出 | Hono JSX、views | 可经 HTTP 输出替代直接复用 | AGPL | `SOURCE_VERIFIED` |
| `lib/api/**` | Namespace/Radar/Category/Status/OpenAPI | OpenAPIHono、Scalar | 主要是元数据 API；中 | AGPL | `SOURCE_VERIFIED` |
| `lib/pkg.ts` | npm library API | 完整 app/registry | 进程内耦合；高许可证耦合 | AGPL | `SOURCE_VERIFIED` |
| `lib/utils/logger.ts` | 文件/控制台日志 | Winston | 简单；低价值 | AGPL | `SOURCE_VERIFIED` |
| `lib/utils/otel/**` | 指标与 trace | OpenTelemetry | 可独立设计；中 | AGPL | `SOURCE_VERIFIED` |
| `lib/views/**` | Feed 与欢迎/错误视图 | Hono JSX | 输出端；中 | AGPL | `SOURCE_VERIFIED` |

## Route 模块内部约定

一个新来源通常包括：

```text
lib/routes/<namespace>/namespace.ts  -> 名称、域名、分类、语言
lib/routes/<namespace>/<route>.ts[x] -> export const route: Route
                                      -> path/name/example/parameters/features/radar
                                      -> handler(ctx): Data
可选 utils/cache/templates/types/tests
```

证据：`lib/routes/cninfo/namespace.ts` + `announcement.ts`、
`lib/routes/jin10/namespace.ts` + `index.ts`，以及
`lib/registry-helpers.ts::applyModulesToNamespaces()`。验证状态：`SOURCE_VERIFIED`。

## 模块边界判断

- 最可复用的不是单个核心函数，而是**独立服务的 HTTP 输出和 Route 生态**。
- 直接抽取 `routes` 会连带 config、cache、request rewriter、类型和 AGPL 义务，表面是单文件，
  实际依赖图并不小。
- 对“即时 AI”更稳妥的界面是 `RSS/Atom/JSON Feed over localhost`；只在许可证审查通过且
  确有必要时评估 npm 包模式。
- Route 稳定性必须逐条维护；不能把源码文件数量等同于所有来源长期可用。
