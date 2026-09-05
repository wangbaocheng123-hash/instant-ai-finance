# ADR-0037：北京采集器使用独立 Git 生产分支与固定定时发布器

日期：2026-09-05。状态：已采纳并完成两次连续生产验收。

## 背景

北京 `blogger-collector` 原来位于独立私有仓库，日常发布依赖电脑版维护端进入服务器。公共 `instant-ai-finance` 已是云端 Codex 可维护的统一源码仓库，但它的 `main` 同时包含即时 AI、新加坡博主域、文档和其他能力。让北京直接跟随每次 `main` 推送会把无关变更变成生产发布，也会扩大云端 Codex 的服务器权限。

本次恢复真实生产后确认安全后继基线为 collector 1.0.7、私有源码提交 `05287e9f6c06736034f23374de6568bc09cb3307`。公开迁移只复制当前文件快照，不导入旧仓库历史；Git 外配置、Cookie、数据库、媒体、评论、outbox、日志和凭据不进入公共仓库。

## 决定

1. 北京可公开源码固定在 `services/beijing-blogger-collector/`。`main` 是开发检查点；`beijing-production` 只有在所有者明确要求正式发布北京采集器时，才从已经测试并推送的 `origin/main` 做安全快进。禁止强推、回退和改写历史；回滚使用新的 revert 提交继续向前发布。
2. 北京服务器每 90 秒以独立低权限用户只读获取公共 HTTPS 仓库的生产分支。只有采集器子树 tree 变化才构建和重启，仓库其他目录的变化只推进已接受提交，不触碰服务。
3. root 持有的固定发布器只编排白名单步骤：锁、快进检查、安全 tree 检查、Git archive、新 release、低权限构建/测试、原子链接、服务重启、健康确认和失败回滚。Git 文件中的部署脚本不会由定时通道自行安装或覆盖正式 publisher/unit。
4. 构建和至少 203 项完整测试由无生产凭据的 `bloggerbuild` 运行，测试阶段使用 `PrivateNetwork=yes`；Git 拉取由 `bloggergit` 运行。拒绝符号链接、特殊文件、越界字符以及数据库、密钥、运行数据路径。失败 SHA 被持久记录，新提交出现前不重复重启。
5. 应用以 `DEPLOYMENT.json` 生成文件记录公开提交和 UTC 部署时间；`/health/version` 只输出 service、status、version、repository revision、deployed time。原 `/health` 保持兼容。
6. Git 通道只发布 `blogger-collector`。模型下载器、Caddy、域名、安全组、证书、数据和凭据均在授权范围之外。首次安装固定基础设施及以后任何基础设施修改，继续通过电脑版 Alibaba Cloud Client 受控维护。

## 回滚与故障语义

新 release 只有在隔离测试通过后才可切换；切换后的回环健康和精确提交检查失败时恢复上一正常 release。首次迁移失败则保留原生产链接和服务。分支分叉、敏感路径、测试数量不足或同一失败 SHA 都会停止发布，不允许 reset、clean、stash 或强推来绕过。

应用回滚在 `main` 创建 revert 提交、重新测试并安全快进至 `beijing-production`。旧 release 和全部 Git 外业务数据保留，发布器不删除它们。
