# ADR-0068：榫卯 AI 社区的新加坡根域备用发布通道

- 状态：已接受，代码就绪，尚未切换生产
- 日期：2026-10-15

## 背景

`阿木爷爷.com` 在中国内地服务器上受到备案状态影响。所有者决定暂时冻结新建的独立社区仓库，继续在既有 `instant-ai-finance` 仓库的 `services/sunmao-ai-community/` 开发，并让手机上的云端 Codex 在明确要求发布时通过新加坡服务器的一次性固定发布器上线。

同一台新加坡服务器已经运行即时 AI、时变罗盘网页和时变罗盘 MCP。所有者明确要求社区与它们保持并列，社区只能接管 `amuyeye.com` 根域名；不得删除、修改或覆盖 `mcp.amuyeye.com` 的 DNS、Caddy 配置或 OAuth 边界，也不得创建可覆盖它的通配符规则。

## 决定

1. 社区源码仍以本仓库 `services/sunmao-ai-community/` 和生产分支 `sunmao-production` 为唯一来源；独立仓库不参与本通道。
2. 新加坡社区继续使用独立 `sunmao-ai-community.service`、`127.0.0.1:18808`、`/opt/sunmao-community` 和 Git 外 `/var/lib/sunmao-community/community.sqlite3`。
3. Caddy 社区站点只声明 `amuyeye.com` 与 `xn--4pv19va7008a.com` 两个精确主机名。明确拒绝 `mcp.amuyeye.com`、`www.amuyeye.com` 和任何通配符。
4. 时变罗盘网页使用已经可访问的 `compass.grandpaamu.com`；即时 AI 保持 `grandpaamu.com`；MCP 保持 `mcp.amuyeye.com`。
5. 社区 Caddy 文件独立为 `/etc/caddy/sunmao-community.caddy`，只由 `/etc/caddy/Caddyfile.instant-ai` 增量导入。脚本不改 `/etc/caddy/Caddyfile`、`/etc/time-compass/caddy.env` 或 MCP 站点。若现有配置仍声明社区根域，组合校验必须在任何写入前失败。
6. `compassdev` 只允许免密执行 root 持有的 `/usr/local/sbin/sunmao-community-git-deploy`，不授予通用 sudo。发布器只读取 `sunmao-production` 的社区子树，只重启社区服务。
7. 不启用分钟轮询、systemd timer 或统一发布器。明确发布后由云端 Codex 触发一次固定发布器。
8. 首次切换必须同时验收两个社区域名、即时 AI、时变罗盘和 MCP。MCP `/health` 必须成功，`/mcp` 必须继续返回带 OAuth challenge 的 401。任何一项失败都恢复 Caddy 备份。

## 结果

- 手机云端 Codex 可以在不依赖新加坡机内安装 Codex 的前提下修改 Git；服务器端只需持有固定发布通道。
- 普通 Git push 仍不等于上线，避免文档提交或误操作自动影响服务器。
- 社区域名切换和以后代码发布分离：首次 Caddy 切换完成后，日常发布不再改域名配置。
- 当前记录只表示代码与安全通道文件就绪。没有服务器执行和公网验收证据前，不得声称已上线。
