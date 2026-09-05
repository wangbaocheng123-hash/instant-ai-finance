# 北京采集器生产发布基础设施

北京采集器运行代码来自公共仓库 `instant-ai-finance` 的 `beijing-production` 分支，固定子树为 `services/beijing-blogger-collector/`。服务器以 `bloggergit` 只读拉取源码，以 `bloggerbuild` 在无生产凭据、无网络的 systemd 沙箱中运行完整测试；`bloggeragent` 继续作为正式运行用户。

正式文件：

- `/usr/local/sbin/blogger-collector-git-deploy`
- `blogger-collector-git-deploy.service`
- `blogger-collector-git-deploy.timer`（90 秒检查）

安装器只用于首次受控迁移，必须从电脑版 Alibaba Cloud Client 的既有北京连接以 root 运行。它只添加两个低权限用户、公共 Git 裸仓、固定发布器和 timer；不会读写 `collector.env`、数据库、媒体、outbox、浏览器资料、模型下载器或 Caddy。

发布器只有在采集器子树 tree 改变时才构建并重启。它拒绝非快进、符号链接、特殊文件、越界路径和运行数据文件；失败提交只尝试一次，新版本健康失败时恢复旧 release。正式配置和业务资料继续位于 Git 外，现有 `blogger-collector.service` 继续从原子链接 `/opt/blogger-agent/current` 启动。

这些基础设施模板不会由定时发布器自行更新。以后修改 publisher、unit 或 timer，仍须通过电脑版 Alibaba Cloud Client 单独审阅和安装。应用发布流程见仓库根目录的 `docs/BEIJING_COLLECTOR_GIT_RELEASE.md`。
