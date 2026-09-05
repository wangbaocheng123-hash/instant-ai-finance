# 北京博主采集器 Git 发布通道

北京采集器的可公开源码固定在 `services/beijing-blogger-collector/`。`main` 保存已经测试的开发检查点；`beijing-production` 只保存用户明确批准正式发布的提交。普通 `main` 推送不会进入北京生产环境。

北京服务器每 90 秒只读拉取公共仓库的 `beijing-production`。固定 root 发布器比较采集器子树的 Git tree：其他目录变化不会构建、重启或改变采集器。采集器子树变化时，发布器验证普通文件与安全路径，将源码提取到新 release，以无生产凭据的 `bloggerbuild` 用户在断网沙箱中运行完整测试，然后原子切换 `/opt/blogger-agent/current` 并做回环健康检查。失败提交会被记录，同一提交不会反复重启；健康检查失败会恢复上一个正常 release。

发布器、timer 和 systemd 正式文件由 root 固定持有。Git 自动发布不会安装或更新这套基础设施，也不会修改模型下载器、Caddy、域名、安全组、业务数据、outbox、媒体、Cookie 或凭据。基础设施变更必须通过电脑版 Alibaba Cloud Client 受控维护。

云端 Codex 的固定流程：

1. 在干净的 `main` 上同步 `origin/main`，修改 `services/beijing-blogger-collector/` 并运行完整测试和敏感文件检查。
2. 提交并推送 `main`。
3. 只有用户明确说“正式发布北京采集器”后，运行 `deploy/beijing/publish-via-git.sh <40位提交SHA>`。
4. 脚本只做安全快进并等待公网 `/health/version` 确认精确提交；首次构建可能需要下载运行依赖，确认窗口为 15 分钟，没有确认时必须报告失败。
5. 用 `deploy/beijing/check-publish-channel.sh` 做只读复查。

回滚通过在 `main` 新建 revert 提交、完成测试并继续安全快进到 `beijing-production`。禁止强推、回退分支或改写历史。

2026-09-05 正式验收：首个失败候选被测试门禁拦截且没有切换生产；`5813df4498d00c50a1191dc812083792541d493e` 修复后首次成功，`7da442128f8aef619c593766a4b62736fb19a4de` 随后在缓存复用条件下完成第二次完整测试和连续发布。公网版本接口精确返回第二个提交，五种图标与 manifest 的摘要和 Git 源文件一致。

同域名登录继续由独立模型下载器提供。服务器通过最小 systemd drop-in 加载 Git 外 root `0600` 的既有网页登录配置和独立 HTTPS Cookie 开关；这些文件不进入 Git。当前登录 release 把永久会话设为 2,592,000 秒，重启后需重新登录一次，之后浏览器在 30 天有效期内直接进入统一首页与采集器。
