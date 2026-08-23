# RSSHub Windows 运行手册（静态拟定，未执行）

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`）。

## 当前验证状态

`NOT_ATTEMPTED`。本子任务严格未安装依赖、未构建、未启动、未访问任何 Route。以下步骤
由固定提交的 `package.json`、`lib/index.ts`、`Dockerfile` 和 Compose 配置推导，不代表
Windows 已验证成功。

## 能否直接运行

源码没有强制 WSL；Node 入口和 scripts 使用 TypeScript、`cross-env`、`tsx`、`tsdown`，
静态上可在 Windows 直接运行。浏览器类 Route 可能需要额外 Chromium/Patchright 或远程
browser endpoint。是否所有依赖均能在本机 Windows 安装必须通过后续受控实验确认。

## 版本与依赖

- Node：`^22.22.2 || ^24.15.0`
- 包管理器：`pnpm@10.34.5`
- 默认端口：`1200`
- Docker：可选；当前环境未安装，安装须用户另行批准
- WSL：源码直接运行不要求；当前不应为 RSSHub 自动安装
- Redis：可选；默认 memory cache 不要求 Redis
- 浏览器：只有需要 Playwright 的 Route 才要求

证据：`package.json.engines/packageManager/scripts`、`config.ts`、Route `features`。

## 建议的首次受控实验

所有改动和依赖只能放 `experiments/RSSHub-lab`，不得在 `upstream/RSSHub-snapshot` 安装或
修改。获得批准后：

```powershell
# 1. 把固定快照复制到 experiments/RSSHub-lab（由主任务按项目规则执行）
# 2. 在实验副本中启用与 package.json 匹配的 pnpm
corepack enable
corepack prepare pnpm@10.34.5 --activate

# 3. 安装锁定依赖（会下载大量 Node 包，须先记录预计成本）
pnpm install --frozen-lockfile

# 4A. 开发入口；registry 可从源码惰性加载
$env:LISTEN_INADDR_ANY='false'
$env:DEBUG_INFO='false'
$env:NO_LOGFILES='true'
$env:CACHE_TYPE='memory'
pnpm dev

# 4B. 或生产入口；必须先构建，因为快照没有 dist/assets/build 产物
pnpm build
pnpm start
```

这些命令没有在本任务中执行。`corepack prepare` 和依赖安装属于外部状态变化，仍需主任务
依据授权执行。

## 最小验证清单

1. `GET http://127.0.0.1:1200/healthz` 返回 `ok`。
2. 主页可访问，但不以主页成功推定 Route 成功。
3. 选择一个无需凭据、无需浏览器的低风险 Route 请求 RSS。
4. 验证 RSS 可解析、item 有稳定 link/guid/pubDate。
5. 再分别验证：巨潮、上交所、金十、财联社、中国黄金协会；雪球和可能需 Cookie 的来源后置。
6. 记录网络请求、Route cache hit、错误、磁盘增量、内存和退出清理。
7. 检查所有日志/缓存是否留在实验目录或 H 盘批准位置。

## 配置建议

- 必须设 `LISTEN_INADDR_ANY=false`，只绑定 `127.0.0.1`。
- 建议 `DEBUG_INFO=false`、`NO_LOGFILES=true`，实验日志由外部重定向到批准目录。
- 保持 `ALLOW_USER_SUPPLY_UNSAFE_DOMAIN=false`。
- 不启用 `OPENAI_API_KEY`、站点 Cookie 或付费账号，除非单独授权并有 secret 管理。
- 只测试公开、授权来源；不绕过付费墙和反爬保护。

## 停止与清理

- 前台运行按 `Ctrl+C`，确认 1200 端口释放。
- 依赖、`dist*`、`assets/build` 和日志均属于实验产物；由主任务先核对绝对路径后按项目清理
  脚本或显式路径移除。
- 不删除上游快照，不在快照中执行清理。
- 如未来使用 Compose：`docker compose down`；只有明确授权后才考虑删除 volume，避免误删。

## 常见静态预期问题

| 问题 | 源码依据 | 处理方向 |
|---|---|---|
| `pnpm start` 找不到 `dist/index.mjs` | 快照无 dist；script 直接执行 dist | 先 `pnpm build` |
| Route 构建产物缺失 | `assets/build` 仅 `.gitkeep` | `pnpm build:routes` 或 `pnpm build` |
| 浏览器 Route 失败 | `features.requirePuppeteer`、Patchright 配置 | 跳过或另批浏览器依赖 |
| Redis 连接失败 | 只有 `CACHE_TYPE=redis` 时需要 | 首测用 memory |
| 来源 403/验证码/空 Feed | Route 有 antiCrawler、Cookie 或上游变化 | 记录为逐 Route 失败，不绕过限制 |
| 局域网暴露 | `LISTEN_INADDR_ANY` 默认 true | 显式设 false |

## Docker 路径

`docker-compose.yml` 会启动 RSSHub、Redis 与 browserless，下载量和磁盘显著大于 Node 最小
实验。当前 Docker 未安装，且项目永久规则要求先报告成本、等待批准。因此此路径仅记录为
`DOC_ONLY_FROM_SOURCE_CONFIG`，不执行。
