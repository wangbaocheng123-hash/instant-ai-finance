# RSSHub 入口清单

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`）。

## 运行与构建入口

| 类型 | 路径/标识符 | 说明 | 状态 |
|---|---|---|---|
| Node 主启动 | `lib/index.ts`，默认导出 `server` | `@hono/node-server.serve` 承载 `app.fetch`；支持 cluster | `SOURCE_VERIFIED` |
| Hono Web 装配 | `lib/app-bootstrap.tsx`，默认导出 `app` | 完整 Node/Vercel 应用和中间件顺序 | `SOURCE_VERIFIED` |
| Node app 兼容入口 | `lib/app.ts` | 先加载 `request-rewriter`，再导出 bootstrap | `SOURCE_VERIFIED` |
| Vercel 入口 | `lib/server.ts` | 同样先加载 request rewriter，再导出 bootstrap | `SOURCE_VERIFIED` |
| Worker 入口 | `lib/worker.ts` | Worker polyfill 与 `app.worker` 导出 | `SOURCE_VERIFIED` |
| Worker app | `lib/app.worker.tsx` | 精简中间件、KV/Browser binding；无 API routes | `SOURCE_VERIFIED` |
| Container Worker | `lib/container.ts`，`RSSHubContainer` | Cloudflare Container 生命周期、20 实例随机分发 | `SOURCE_VERIFIED` |
| npm 包入口 | `lib/pkg.ts`，`init/request/registerRoute` | 进程内请求与动态注册 Route | `SOURCE_VERIFIED` |
| 构建入口 | `scripts/workflow/build-routes.ts` | 扫描 route 源码并生成构建期 registry、radar、类型 | `SOURCE_VERIFIED` |

## 命令行入口

项目没有独立参数解析 CLI；命令入口来自 `package.json.scripts`：

- `pnpm dev`：`tsx watch ... lib/index.ts`
- `pnpm build`：先 `build:routes`，再 `tsdown`
- `pnpm start`：`node dist/index.mjs`，要求先生成 `dist`
- `pnpm test`：格式检查后运行 Vitest coverage
- `pnpm vitest:fullroutes`：基于 Route examples 执行全路由测试
- `pnpm worker-build/worker-dev/worker-deploy`：Cloudflare Worker 流程
- `pnpm container-build/container-deploy`：Cloudflare Container 流程

上述命令只从源码配置确认，未执行。快照没有 `dist` 和 route 构建产物，不能直接执行
`pnpm start`。

## Web 与 API 入口

| URL | 注册源码 | 作用 |
|---|---|---|
| `/` | `lib/registry.ts` → `lib/routes/index.tsx` | 欢迎页 |
| `/<namespace>/<route>` | `lib/registry-helpers.ts::registerRssRoutes` | 主 RSS/Atom/JSON Feed 路由 |
| `/api/<namespace>/<apiRoute>` | `registerApiRoutes` | 路由自定义 API handler |
| `/api/openapi.json` | `lib/api/index.ts` | 平台元数据 API 的 OpenAPI 3.1 文档 |
| `/api/reference` | `lib/api/index.ts` | Scalar API reference |
| `/healthz` | `lib/routes/healthz.ts` | 健康检查，返回 `ok` |
| `/metrics` | `lib/registry.ts`、`lib/routes/metrics.ts` | 非 `DEBUG_INFO=false` 时提供指标 |
| `/robots.txt` | `lib/registry.ts` | robots 响应 |

## Docker 入口

- `Dockerfile`：最终镜像 `EXPOSE 1200`，`ENTRYPOINT ["dumb-init", "--"]`，
  `CMD ["npm", "run", "start"]`。
- `docker-compose.yml`：RSSHub + Redis + browserless，映射 `1200:1200`，Redis 使用
  `redis-data` volume。
- Docker 是可选部署路径，不是源码运行的强制条件；本子任务没有安装或运行 Docker。

## 定时任务入口

未发现采集 cron/scheduler 或主动轮询入口。RSSHub 路由在收到 HTTP 请求时抓取，缓存命中
时可避免再次抓取。需要外部阅读器或“即时 AI”调度器定期请求。

## 关键配置入口

- `package.json`：运行时、脚本、依赖、Node/pnpm 版本。
- `lib/config.ts`：环境变量解析与默认值。
- `.env`：由 `dotenv/config` 读取；快照中没有密钥模板文件。
- `docker-compose.yml`：Redis、browserless 与端口组合。
- `wrangler.toml`、`wrangler-container.toml`：Worker/Container binding。
- `tsconfig.json`：`@/* -> ./lib/*` 与 ESM/TS 配置。
- `tsdown*.config.ts`：各部署目标打包配置。
- `vitest.config.ts`、`vitest.workers.config.ts`：Node 与 Worker 测试入口。
