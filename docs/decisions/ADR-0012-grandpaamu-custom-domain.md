# ADR-0012：`grandpaamu.com` 作为即时 AI 正式手机域名

- 状态：`ACCEPTED`
- 日期：2026-08-28
- 决策者：用户指定两个空闲域名可用，并针对当前 `grandpaamu.com` DNS 页面作出一次性明确授权

## 背景

即时 AI 已通过 `instant-ai.47-236-175-118.sslip.io` 提供 HTTPS 手机入口，但该地址依赖服务器公网 IP，不适合作为长期易记入口。用户展示的 `grandpaamu.com` 与中文域名均处于空闲状态；选择 ASCII 域名可以避免部分浏览器、证书和主屏幕图标环境中的国际化域名兼容差异。

默认阿里云操作规则仍是只使用桌面 `Alibaba Cloud Client`。该客户端不含 AliDNS 记录管理能力。用户在获知这一限制后，只针对当时已经打开的 `grandpaamu.com` DNS 页面明确回复“允许”，构成一次性、窄范围例外；它不授权控制其他浏览器页面、账户或以后任务。

## 决定

1. `https://grandpaamu.com/` 作为即时 AI 正式手机入口。
2. `grandpaamu.com` 的 `@` 与 `www` A 记录均指向现有即时 AI 云服务器 `47.236.175.118`，TTL 为 10 分钟。
3. 根域名直接提供即时 AI；`https://www.grandpaamu.com/` 永久跳转到根域名，避免形成两个 PWA 数据源。
4. `https://instant-ai.47-236-175-118.sslip.io/` 继续保留为应急入口，不删除、不替换。
5. 只修改即时 AI 独立的 `/etc/caddy/instant-ai.caddy`；原 `/etc/caddy/Caddyfile`、Time Compass、`127.0.0.1:8080` 服务和其他云端文件保持不变。
6. Caddy 自动签发与续期根域名及 `www` 证书。即时 AI 仍只监听 `127.0.0.1:18765`，公网无注册、账户或密码，API 继续 `no-store`。
7. 本次 DNS 页面操作完成后，阿里云操作恢复 `Alibaba Cloud Client` 优先规则；任何未来网页操作必须重新取得当前对象的一次性明确授权。

## 验收证据

- AliDNS 控制台显示 `@` 与 `www` 均为启用的 A 记录 `47.236.175.118`。
- 阿里云公共 DNS 与 Cloudflare DNS 均解析根域名到 `47.236.175.118`。
- `https://grandpaamu.com/`、`/api/health`、`/api/status`、manifest 和 Service Worker 返回 HTTPS 200；健康接口版本为 0.8.2。
- `https://www.grandpaamu.com/` 返回 301 并跳转根域名；旧 sslip.io 健康接口继续返回 200。
- Caddy 与 `instant-ai.service` 均为 active；Time Compass 本机健康状态在 Caddy 重启后仍为 200。

## 影响

- iPhone 可以用短域名添加到主屏幕，重新打开、回到前台和恢复网络时继续读取最新数据。
- 域名是公开入口；任何知道地址的人都可以访问公开财经消息，但云端不包含 H 盘资料、账户数据或个人文件。
- DNS 或公网 IP 将来发生变化时，需要同步更新 AliDNS A 记录；应急 sslip.io 地址仅在服务器公网 IP 不变时有效。
