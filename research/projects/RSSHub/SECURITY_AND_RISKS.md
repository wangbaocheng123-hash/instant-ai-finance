# RSSHub 安全与风险

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`）。

## 最高优先级

### 1. 默认监听所有网卡

- 源码：`lib/config.ts::listenInaddrAny`，默认 `true`。
- 调用：`lib/index.ts` 选择 `::`/`0.0.0.0`；只有 false 才绑定 `127.0.0.1`。
- 风险：个人电脑上的 Feed 路由、debug、metrics 和可能带凭据的抓取能力可能被局域网访问。
- 缓解：本机部署强制 `LISTEN_INADDR_ANY=false`，防火墙和进程权限最小化。
- 状态：`SOURCE_VERIFIED`。

### 2. 用户可控 URL 与 SSRF 面

部分 transform/通用 Route 能从 path 参数构造或直接请求 URL。源码用
`ALLOW_USER_SUPPLY_UNSAFE_DOMAIN=false` 默认禁用部分危险 Route，但该保护是 Route 级约定，
并非 request rewriter 的统一 DNS/IP 网络边界。`valid-host.ts` 只验证单段 hostname 语法，
不做解析后私网地址阻断。

- 缓解：保持该开关 false；只启用 Route 白名单；sidecar 运行于低权限网络沙箱；对出站地址
  做统一 allowlist/DNS rebinding 防护；不对公网暴露。
- 状态：`SOURCE_VERIFIED` + 风险推断。

### 3. 凭据与隐私

`config.ts` 支持大量 Cookie、token、用户名、密码和 API key；路由可能把它们发给第三方站点。
OpenAI 后处理会把标题或正文发往配置的外部 endpoint。

- 缓解：只注入所需凭据；secret store；禁入 Git/日志；默认关闭 OpenAI；敏感正文不得发送给
  外部模型；账号不得用于绕过付费墙。
- 状态：`SOURCE_VERIFIED`。

### 4. 上游内容不可信

`parameter.ts` 会移除 `<script>`、处理链接和部分图片事件属性，但不是对所有 HTML 属性的
严格 allowlist sanitizer。`template.tsx` 还有 debug HTML 输出。Feed 的正文来自外部站点，
下游必须视为不可信 HTML。

- 缓解：即时 AI 入库时保存原文与净化版本；UI 使用严格 CSP 和 sanitizer；禁止 WebView
  直接执行 Feed HTML；debug 生产关闭。
- 状态：`SOURCE_VERIFIED` + 风险推断。

## 中高风险

### 来源稳定性、反爬和合法性

Route metadata 可标记 `antiCrawler`、`requirePuppeteer`、`requireConfig`。雪球路由明确设置
`antiCrawler: true`；其他来源可能依赖 Cookie、隐藏 API 或 HTML 结构。RSSHub 可运行不代表
单个 Route 可用或获得授权。

缓解：逐 Route 运行验收、速率限制、来源条款记录、失败熔断；不绕过验证码、付费墙或访问
控制。

### 资源放大与拒绝服务

- `mode=fulltext` 会按 item 抓详情；`chatgpt` 会逐 item 调外部 API；浏览器 Route 更重。
- `parameter.ts` 使用 `Promise.all`，可在大 Feed 上产生并发放大。
- route cache 有并发 claim，但 HTTP/KV 实现是 best-effort；无缓存时源码明确警告并发请求
  不受限制。

缓解：localhost + access key；外部调度限速；禁用不需参数；限制 item 数；资源配额和超时。

### 远端配置供应链

`config.ts` 可从 `REMOTE_CONFIG` 拉配置并覆盖环境值。若 endpoint 被控制，可能改变代理、
凭据或功能配置。

缓解：本机方案禁用；如必须启用，使用 TLS、固定域名/证书策略、最小配置范围和审计。

### 日志与磁盘边界

Winston 默认写工作目录 `logs/*.log`。这既可能违反研究仓库/业务数据分离，也可能长期增长。

缓解：实验设 `NO_LOGFILES=true` 并由受控外部日志捕获；正式部署映射 H 盘日志目录并轮转。

## 供应链与部署风险

- `package.json` 直接依赖数量多，并包含 patches、tarball override、浏览器依赖和多个 SDK；安装
  需要联网并执行允许的 build scripts。
- Dockerfile 可下载/安装 Chromium 与大量 Debian 包；Compose 还启动 Redis、browserless。
- Worker 形态有功能裁剪，不能假设与 Node 一致。
- 固定快照无 `.git`，无法在目录内运行 `git verify`、查看历史或确认签名。

缓解：只在实验副本锁文件安装；记录包下载和磁盘变化；先做依赖漏洞/许可证审计；优先最小
Node + memory 路径；浏览器/容器需另批。

## 访问控制与可观测性

`middleware/access-control.ts` 支持 `ACCESS_KEY` 或基于 path 的 md5 code。它是查询参数式
访问控制，不替代 TLS、网络绑定和现代认证。`logger.ts::getPath` 会去掉查询字符串，静态上
不会把 `?key=` 直接写入标准请求日志，但反向代理或其他日志仍可能记录完整 URL。

建议：只本机监听；若跨网络，使用反向代理鉴权和 TLS，不在 URL 传长期秘密。

## 项目安全声明

`SECURITY.md` 只声明支持 master 最新提交并提供 GitHub Security Advisory/邮箱报告方式。
这是 `DOC_ONLY` 的维护流程信息，不能替代固定提交的漏洞扫描。

## 即时 AI 采用红线

- 不启用任意域名抓取开关。
- 不把 Cookie、API key、日志、缓存放进研究仓库。
- 不把外部 HTML 直接放入可执行 WebView。
- 不用 RSSHub 绕过付费墙或反爬访问控制。
- 不把启动成功等同于全部财经 Route 可用。
- 不在未完成 AGPL 和来源条款审查前分发修改版或内嵌版。
