# 榫卯 AI 社区

这是 `instant-ai-finance` 公共仓库中的独立应用，不是北京博主采集器的页面或子模块。

它可以部署在北京或新加坡服务器，但始终是独立进程，不并入即时 AI、时变罗盘、北京博主采集器或模型下载器：

- 社区源码：`services/sunmao-ai-community/`
- 社区进程：`sunmao-ai-community.service`
- 回环端口：`127.0.0.1:18808`
- 运行数据库：`/var/lib/sunmao-community/community.sqlite3`
- 原子发布目录：`/opt/sunmao-community/`
- 生产分支：`sunmao-production`
- 社区精确公网域名：`https://amuyeye.com/`、`https://阿木爷爷.com/`

北京博主采集器继续使用自己的目录、`blogger-collector.service`、端口 `18797`、数据目录和 `beijing-production`；新加坡即时 AI、时变罗盘和 `mcp.amuyeye.com` 也继续使用各自的服务、端口、配置和数据。发布社区不得重启或改写这些并列应用。

## 本地运行

```powershell
cd services/sunmao-ai-community
python -m unittest discover -s tests -p "test_*.py"
python -m sunmao_community --host 127.0.0.1 --port 4173 --database .runtime/community.sqlite3
```

## 发布规则

普通 `main` 推送只保存开发检查点，不上线。所有者明确说“更新并发布榫卯AI社区：<具体需求>”后，才允许把已验收提交安全快进到 `sunmao-production`，再触发固定的一次性 `sunmao-community-git-deploy.service`。该发布只切换 `/opt/sunmao-community/current` 并重启社区进程。

首次从目前“由采集器代为提供首版页面”的临时拓扑迁移到独立服务，需要一次性安装独立服务、发布器和 Caddy 反向代理；迁移前必须先确认新服务在 `127.0.0.1:18808` 健康，Caddy 切换失败则恢复备份。以后普通社区功能发布不再修改 Caddy。

## 新加坡备用发布通道

备案问题处理期间，独立仓库保持冻结；继续从本仓库的 `services/sunmao-ai-community/` 开发。`deploy/singapore/` 提供加法式安装与验收文件：

- 社区只接管精确的 `amuyeye.com` 与 `xn--4pv19va7008a.com`，不创建通配符，也不声明 `www`；
- `mcp.amuyeye.com` 仍由时变罗盘 MCP 的精确站点服务，切换前后都必须通过健康与 OAuth 401 边界检查；
- 时变罗盘网页固定使用 `compass.grandpaamu.com`，即时 AI 继续使用 `grandpaamu.com`；
- Caddy 只新增 `/etc/caddy/sunmao-community.caddy` 并在既有 `/etc/caddy/Caddyfile.instant-ai` 中增加一条 import，不修改 `/etc/caddy/Caddyfile` 或 `/etc/time-compass/caddy.env`；若旧配置仍占用 `amuyeye.com`，预验证会在写入前停止；
- `compassdev` 只获得运行 `/usr/local/sbin/sunmao-community-git-deploy` 的固定 sudo 权限。没有通用 root 权限，不安装 Git 定时器；手机云端 Codex 在明确发布后触发一次即可。

首次安装和域名切换属于服务器运维，必须在服务器端完整通过 `verify-cutover.sh` 后才可记录为上线；仅提交 Git 不等于上线。
