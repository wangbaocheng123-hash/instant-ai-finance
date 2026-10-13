# 榫卯 AI 社区

这是 `instant-ai-finance` 公共仓库中的独立应用，不是北京博主采集器的页面或子模块。

两者可以部署在同一台北京服务器，但永久保持并列：

- 社区源码：`services/sunmao-ai-community/`
- 社区进程：`sunmao-ai-community.service`
- 回环端口：`127.0.0.1:18808`
- 运行数据库：`/var/lib/sunmao-community/community.sqlite3`
- 原子发布目录：`/opt/sunmao-community/`
- 生产分支：`sunmao-production`
- 公网域名：`https://阿木爷爷.com/`

北京博主采集器继续使用自己的目录、`blogger-collector.service`、端口 `18797`、数据目录和 `beijing-production`；发布社区不得重启或改写采集器。

## 本地运行

```powershell
cd services/sunmao-ai-community
python -m unittest discover -s tests -p "test_*.py"
python -m sunmao_community --host 127.0.0.1 --port 4173 --database .runtime/community.sqlite3
```

## 发布规则

普通 `main` 推送只保存开发检查点，不上线。所有者明确说“更新并发布榫卯AI社区：<具体需求>”后，才允许把已验收提交安全快进到 `sunmao-production`，再触发固定的一次性 `sunmao-community-git-deploy.service`。该发布只切换 `/opt/sunmao-community/current` 并重启社区进程。

首次从目前“由采集器代为提供首版页面”的临时拓扑迁移到独立服务，需要一次性安装独立服务、发布器和 Caddy 反向代理；迁移前必须先确认新服务在 `127.0.0.1:18808` 健康，Caddy 切换失败则恢复备份。以后普通社区功能发布不再修改 Caddy。
