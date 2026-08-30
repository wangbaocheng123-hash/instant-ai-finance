# ADR-0019：云端 Codex 使用受限发布通道

- 状态：`ACCEPTED`
- 日期：2026-08-30
- 决策者：用户要求恢复此前已经多次正常工作的云端 Codex 自动发布，不再重复人工粘贴服务器命令

## 背景

即时 AI 与时变罗盘都部署在同一台阿里云服务器，云端 Codex 进程以 `compassdev` 运行。服务器此前已经建立两个彼此隔离的 root 持有发布器，并在其他 Codex 任务中多次自动发布成功：

- 即时 AI：`/usr/local/sbin/instant-ai-publish`
- 时变罗盘：`/usr/local/sbin/time-compass-publish` 与独立回滚器

2026-08-30 的即时 AI 任务没有先读取 sudo 精确白名单，而是用 `sudo -n true` 测试任意 root 权限；同时，本仓库永久说明只写了 Alibaba Cloud Client 和底层 root 更新脚本，没有记录已经安装的受限发布器。两项叠加导致任务把“禁止通用 root”误判成“没有应用发布权限”，让用户进行了一次本可避免的手动操作。

## 决定

1. 阿里云控制面和服务器内应用发布分开管理。实例、安全组、DNS、证书和网页控制台继续遵守 Alibaba Cloud Client 规则；已经位于目标服务器上的 Codex 发布即时 AI，不属于控制面操作。
2. 用户明确要求“正式发布”即时 AI 后，云端 Codex 直接执行 `sudo -n /usr/local/sbin/instant-ai-publish`，不再要求用户登录 root 终端或粘贴底层脚本。
3. 即时 AI 发布器必须由 `root:root` 持有且不可被组或其他用户写入。sudoers 只列出 `compassdev` 以 root 执行这一个绝对路径，没有列出 shell、编辑器、`systemctl`、其他脚本或通用 sudo。
4. 发布器只允许生产仓库 `main` 的干净 fast-forward 更新，运行仓库内 Python 测试，只重启 `instant-ai.service`，并等待 `127.0.0.1:18765/api/health` 成功。工作树不干净、测试失败、服务失败或健康失败均立即返回失败。
5. 每次发布前运行 `deploy/aliyun/check-publish-channel.sh`。它只读核对文件所有权/模式、精确 sudo 白名单、生产仓库分支/远程/清洁度、发布器与底层脚本哈希、服务与回环健康，并以 `CODEX_CLOUD_PUBLISH_READY` 表示可用。
6. 判断发布权限时使用 `sudo -n -l` 或对精确绝对路径使用 `sudo -n -l /usr/local/sbin/instant-ai-publish`。`sudo -n true` 测试的是未授权的任意 root 命令，失败是正确安全行为，不能据此宣称发布器不可用。
7. 时变罗盘的发布器、回滚器、仓库和项目规则保持独立。即时 AI 发布流程不得调用或修改它们；跨系统正式发布必须分别取得用户当前指令并分别遵守各仓库规则。

## 可恢复性

仓库保存 sudoers 最小规则模板与 `install-codex-publish-channel.sh`。若服务器重建或自检发现发布器与仓库脚本漂移，由 root 操作员运行一次安装器；安装器先用 `visudo` 验证，再安装 root 持有文件，并从 `compassdev` 身份验证精确白名单。正常版本发布不运行安装器。

## 验收证据

- `/etc/sudoers.d/compassdev-instant-ai-publish` 为 `root:root 0440`，只授权 `/usr/local/sbin/instant-ai-publish`。
- 即时 AI 发布器为 `root:root 0755`；审计时其 SHA-256 与生产仓库 `deploy/aliyun/update-instant-ai.sh` 一致。
- `sudo -n -l /usr/local/sbin/instant-ai-publish` 成功；任意 root 命令仍未获授权。
- 尝试从调用端注入 `INSTANT_AI_REPOSITORY_ROOT` 被 sudo 明确拒绝，不能把发布器重定向到其他仓库目录。
- 历史 Codex 会话记录确认即时 AI 与时变罗盘均曾通过各自受限发布器自动发布。
- 即时 AI、Caddy、时变罗盘主服务、MCP 和隧道在审计时均为 active；两套系统仍保持独立。

## 与既有决定的关系

本 ADR 澄清 AGENTS、项目章程和 ADR-0011 中的阿里云入口规则：Alibaba Cloud Client 限制适用于控制面操作，不废除服务器上已安装的最小权限应用发布器。它不改变只新增部署、单用户、无账户、短周期数据和两套系统隔离边界。
