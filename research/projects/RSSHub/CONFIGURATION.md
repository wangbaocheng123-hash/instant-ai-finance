# RSSHub 配置

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`）。

## 配置入口与加载

`lib/config.ts` 通过 `dotenv/config` 读取 `process.env`，`calculateValue()` 转为 `Config`。
`setConfig()` 供 npm 包模式覆盖环境变量。若设置 `REMOTE_CONFIG`，模块会异步用 `ofetch`
拉取远端配置，并可通过 Basic Authorization 传 `REMOTE_CONFIG_AUTH`。

远端配置会引入启动时网络依赖和密钥边界，正式本机方案不应默认启用。

## 核心配置与源码默认值

| 配置 | 默认/行为 | 源码证据 | 风险或用途 |
|---|---|---|---|
| `PORT` | `1200` | `config.ts::connect.port` | 服务端口 |
| `LISTEN_INADDR_ANY` | `true` | `config.ts::listenInaddrAny` | 默认监听所有网卡；个人本机应设 `false` |
| `DISABLE_IPV6` | `false` | `config.ts`、`index.ts` | all-interface 时决定 `::` 或 `0.0.0.0` |
| `ENABLE_CLUSTER` | `false` | `config.ts`、`index.ts` | 开启后按可用 CPU fork |
| `CACHE_TYPE` | `memory` | `config.ts::cache.type` | 可设 memory/redis/http/空字符串 |
| `CACHE_EXPIRE` | `300` 秒 | `config.ts::routeExpire` | 整路由缓存 |
| `CACHE_CONTENT_EXPIRE` | `3600` 秒 | `config.ts::contentExpire` | 详情内容缓存 |
| `MEMORY_MAX` | `256` 项 | `config.ts::memory.max` | LRU 容量 |
| `REDIS_URL` | `redis://localhost:6379/` | `config.ts` | Redis cache |
| `ACCESS_KEY` | 未设置 | `config.ts`、`middleware/access-control.ts` | 通过 `?key=` 或 path code 限制访问 |
| `DEBUG_INFO` | 字符串 `'true'` | `config.ts` | 控制 debug/metrics；本机生产建议 `false` |
| `NO_LOGFILES` | `false` | `config.ts`、`utils/logger.ts` | 默认写工作目录 logs |
| `FORMAT` | `rss` | `config.ts`、`template.tsx` | 默认输出格式 |
| `FILTER_REGEX_ENGINE` | `re2` | `config.ts`、`parameter.ts` | 避免普通 RegExp 的部分 ReDoS 风险 |
| `ALLOW_USER_SUPPLY_UNSAFE_DOMAIN` | `false` | `config.ts` | 开启后解锁任意 URL 类 Route，增大 SSRF 风险 |
| `ALLOW_USER_HOTLINK_TEMPLATE` | `false` | `config.ts`、`anti-hotlink.ts` | 用户可改媒体 URL 模板 |
| `DISABLE_NSFW` | `false` | `config.ts`、`registry.ts` | 生产 registry 可剔除 NSFW route |

## OpenAI 配置

`lib/middleware/parameter.ts::getAiCompletion()` 调用
`${OPENAI_API_ENDPOINT}/chat/completions`。主要配置：

- `OPENAI_API_KEY`
- `OPENAI_MODEL`，默认 `gpt-3.5-turbo-16k`
- `OPENAI_TEMPERATURE`，源码用整数解析，默认 `0.2`；该实现值得运行测试核对
- `OPENAI_MAX_TOKENS`
- `OPENAI_API_ENDPOINT`，默认 `https://api.openai.com/v1`
- `OPENAI_INPUT_OPTION`，默认 `description`，也支持 title/both
- `OPENAI_PROMPT`、`OPENAI_PROMPT_TITLE`

只有请求带 `chatgpt` 且存在 API key 时触发。API key 不得进入仓库；正式系统应在自己的
AI 层管理模型、预算、提示词版本和审计，而不是依赖 Feed URL 参数。

## 抓取、代理与浏览器

关键项包括 `REQUEST_RETRY`、`REQUEST_TIMEOUT`、`UA`、`PROXY_URI/URIS`、
`PROXY_*`、`PAC_URI/SCRIPT`、`PLAYWRIGHT_WS_ENDPOINT`、
`PLAYWRIGHT_CDP_ENDPOINT`、`CHROMIUM_EXECUTABLE_PATH`。`request-rewriter` 会全局替换
fetch 与 Node http(s) 请求函数，统一 UA、限速和代理。

## Route 凭据

`config.ts` 还读取大量站点 Cookie、token、用户名/密码和 API key，例如
`CAIXIN_COOKIE`、`XUEQIU_COOKIES`、`GITHUB_ACCESS_TOKEN`、`YOUTUBE_KEY` 等。
Route 通过 `features.requireConfig` 声明需要的变量。规则：

- 所有凭据只放运行环境或专用 secret store；
- 不写入 `.env` 后提交；
- 不使用订阅凭据绕过付费墙；
- 独立 RSSHub 进程只配置实际启用 Route 所需最小权限。

## 推荐的本机最小配置（尚未运行验证）

```dotenv
PORT=1200
LISTEN_INADDR_ANY=false
DEBUG_INFO=false
NO_LOGFILES=true
CACHE_TYPE=memory
ALLOW_USER_SUPPLY_UNSAFE_DOMAIN=false
ALLOW_USER_HOTLINK_TEMPLATE=false
```

若 RSSHub 只绑定 `127.0.0.1`，`ACCESS_KEY` 仍可作为纵深防护；若任何情况下对局域网或公网
暴露，必须启用访问控制、来源白名单、反向代理限制并做独立安全评审。
