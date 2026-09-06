# 北京博主采集器 Codex Git 直连更新

北京采集器的可公开源码固定在 `services/beijing-blogger-collector/`。`main` 保存已经测试的开发检查点；`beijing-production` 只保存用户明确批准正式发布的提交。普通 `main` 推送不会进入北京生产环境。

北京服务器不再定时轮询 Git。只有 Codex 收到固定发布口令并安全推进 `beijing-production` 后，才通过电脑版 Alibaba Cloud Client 的“发送远程命令”启动一次 `blogger-collector-git-deploy.service`。固定 root 发布器比较采集器子树的 Git tree：其他目录变化不会构建、重启或改变采集器。采集器子树变化时，发布器验证普通文件与安全路径，将源码提取到新 release，以无生产凭据的 `bloggerbuild` 用户在断网沙箱中运行完整测试，然后原子切换 `/opt/blogger-agent/current` 并做回环健康检查。失败提交会被记录，同一提交不会反复重启；健康检查失败会恢复上一个正常 release。

发布器和一次性 systemd service 正式文件由 root 固定持有。Git 发布不会安装或更新这套基础设施，也不会修改模型下载器、Caddy、域名、安全组、业务数据、outbox、媒体、Cookie 或凭据。原 90 秒 timer 必须保持 `disabled` 与 `inactive`；基础设施变更必须通过电脑版 Alibaba Cloud Client 受控维护。

## 给 Codex 的固定口令

以后只要发送：

> 更新并发布北京博主采集器：<具体需求>

这句话同时授权 Codex 在同一任务里完成下面全部步骤。所有者不需要再打开 Alibaba Cloud Client 或服务器终端。若只要求“修改”或“提交 Git”而没有发布含义，Codex 只推送 `main`，不更新正式服务器。

## Codex 的固定执行流程

1. 在干净的 `main` 上同步 `origin/main`，修改 `services/beijing-blogger-collector/` 并运行完整测试和敏感文件检查。
2. 提交并推送 `main`。
3. 只有用户明确说“更新并发布北京博主采集器”或“正式发布北京采集器”后，运行 `deploy/beijing/publish-via-git.sh <40位提交SHA>`；脚本安全快进生产分支并等待公网精确版本。
4. 脚本开始等待后，Codex 使用电脑版 Alibaba Cloud Client 的“发送远程命令”，只发送 `systemctl start --no-block blogger-collector-git-deploy.service`，触发这次发布。不得启用 timer，也不得在服务器上直接编辑应用代码。
5. 等待 `/health/version` 确认目标提交；确认窗口为 15 分钟，没有确认时必须报告失败。随后用 `deploy/beijing/check-publish-channel.sh` 做只读复查。

回滚通过在 `main` 新建 revert 提交、完成测试并继续安全快进到 `beijing-production`。禁止强推、回退分支或改写历史。

2026-09-05 正式验收：首个失败候选被测试门禁拦截且没有切换生产；`5813df4498d00c50a1191dc812083792541d493e` 修复后首次成功，`7da442128f8aef619c593766a4b62736fb19a4de` 随后在缓存复用条件下完成第二次完整测试和连续发布。公网版本接口精确返回第二个提交，五种图标与 manifest 的摘要和 Git 源文件一致。

30 天登录和“指定一条视频”是这套 Git 直连更新建立前已有的能力，不属于本次建设。后续使用本口令时不得重复实现或顺带修改这两项功能。

2026-09-06 运行状态：原 90 秒 timer 已停用并复核为 `disabled/inactive`，正式采集器服务保持 `active`。以后没有发布指令时，北京服务器不会检查 Git。
