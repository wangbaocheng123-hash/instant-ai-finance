# 北京受限 SSH 控制：首次启用与手机使用

状态：开发实现，**尚未服务器安装或端到端验证**。不要把本文当作已接通凭证。
架构见 ADR-0046；本次不推进 `beijing-production`，不发布业务，不修改 timer。

## 一次性启用闸门

1. 在可信 Alibaba Cloud Client 确认北京轻量实例
   `e905b859a6c24c529ef5c2ea21658462 / 47.93.214.76` 及所属账号。
   另确认新加坡 ECS `i-t4nikvwazxwfaghfb7ez`。账号未核实时不能代填。
2. 由**新加坡 Codex**只读确认真实 Git 根/远程/工作区、当前用户、出口与到北京 22 端口的
   连接；由北京既有 root 终端确认 sshd 与固定发布器。Windows 的成功或失败不作跨区证据。
   不需要 RAM 角色，不读取云客户端配置、令牌或 .env，不打开网页控制台。
3. 只在新加坡、以实际 Codex 运行用户生成专用 Ed25519 密钥（不能在 Windows 生成再复制）：

   ```sh
   install -d -m 0700 "$HOME/.config/instant-ai-beijing-control"
   ssh-keygen -t ed25519 -N '' -C beijing-control -f "$HOME/.config/instant-ai-beijing-control/ssh_identity"
   ```

   **先检查文件是否存在**；存在则复用，不接受覆盖提示。私钥不显示、不上传、不入 Git。
   无口令专用服务密钥的权限由文件 0600、固定来源和北京强制命令共同限制。
   只把 `.pub` 公钥经可信通道交给北京 root。不要从 SSH agent 或桌面客户端导出密钥。
4. 由北京既有 root 连接读出 `/etc/ssh/ssh_host_ed25519_key.pub` 的**公钥和 SHA256 指纹**；
   不读同名无 `.pub` 私钥。新加坡 `known_hosts` 文件只存该公钥，主机别名为
   `beijing-collector-control`，权限 0600。不能单独信任网络扫描得到的 key。
5. 北京 root 在 root 持有的干净独立源码 checkout 取得本次**已审查精确提交**，不能使用
   浮动 main 自动取新代码；GitHub 访问失败立即报告，不自行配置 VPN 或镜像。该源码 checkout
   仅供安装，不是生产 checkout，也不能触发发布。先运行：

   ```sh
   python3 deploy/beijing/ssh-control/install.py --revision <本次已核验完整SHA> --source-ip 47.236.175.118 --public-key-file <专用公钥.pub>
   ```

   输出 `...PREFLIGHT_OK apply=false` 仍不是已启用。确认精确目标和现场权限后用同一参数加
   `--apply`；若经 Windows UI 建立持久授权，执行前还须按该工具要求取得当次确认。
   安装器只增补七个控制文件和专用账户，拒绝覆盖不同文件、换公钥或迁移既有不同用户。
   旧阶段 A 用户若已创建为 `/bin/bash` 或拥有用户可写家目录，会明确停止，需要单独核实
   后进行窄范围迁移，不能强行重装。
6. `sshd -t`、目标 Match 生效配置、root/其他用户不变、精确 sudo 权限和只读包装器先验证，
   才 reload 现有 SSH 服务。任何失败恢复本次新增授权文件；已经新建的锁定账户保留并报告，
   不删除家目录或业务文件。现有 root 连接保持打开，以便撤销。
7. 从新加坡执行下面的 `inspect` 和 `status`，再验证 shell/SFTP/PTY/转发被拒绝。
   不测试真实 `publish` 来证明只读通道；首次真实发布仍需主人明确授权。

## 云端 Codex 的固定命令

在真实即时 AI Git 工作区（不是罗盘工作区）执行：

```sh
python3 deploy/beijing/ssh-control/client.py inspect
python3 deploy/beijing/ssh-control/client.py status
```

`inspect` 输出服务与 Git 元数据；`status` 输出版本/状态白名单，不读取日志、Cookie、
环境变量配置或业务正文。新加坡专用密钥/主机公钥不存在或权限不安全时直接停止。

收到主人**明确发布指令**、完成需求/测试/审查并提交到 main 后才运行：

```sh
python3 deploy/beijing/ssh-control/client.py publish <完整40位SHA>
```

它先验证通道、生产 timer 和当前健康，再安全快进生产分支、触发一次、最长15分钟核对回执。
连接不确定时不会反复触发；恢复连接后只查同一个 SHA：

```sh
python3 deploy/beijing/ssh-control/client.py verify <相同完整SHA>
```

`BEIJING_SSH_PUBLISH_VERIFIED` 才表示实际版本验收通过。`...TRIGGERED` 只是已发送。
无人值守禁止 timer；若发现 timer enabled/active，客户端停止并报告，不自动改它。
两条生产任务不得并行推进分支；发现并发改动只报告，不强推或回退分支。

## 手机上直接说

> 在新加坡的即时 AI 仓库内处理北京博主采集器：〈具体需求〉。先阅读 AGENTS、STATUS 和
> ADR-0046，执行受限 SSH 的 inspect/status。只修改 services/beijing-blogger-collector
> 的相关代码并测试；保留现有视频传输、数据与采集设置。这次先不发布。

需要上线时另说：

> 正式发布刚才已审查通过的北京博主采集器提交〈完整SHA〉，通过受限 SSH 触发一次并验收
> 真实运行版本。不要发布新加坡即时 AI、时变罗盘或修改模型下载器，不启用 timer。

首次把真实即时 AI 文件夹保存为新加坡 Codex 项目。以后从该项目开新任务，不需要每天选择
多层目录。项目外的普通聊天不会被假定具有正确 Git 根或发布权限。

## 保留的能力与尚未完成

- 现有 Beijing→Singapore 签名 HTTPS 视频/评论传输不改；与 SSH 管理通道各自认证。
- 现有 root Git 发布器、隔离构建、健康失败恢复和 Cloud CLI 不卸载不重装。
- 目前只纳管博主采集器；原模型下载器真实源码还没有核实导入，不能直接承诺修改它。
- 当前未做：服务器首次安装、真实主机/权限/跨区验证、电脑关机手机验收、真实发布与回滚演练。

本地验证命令（只用合成数据）：

```sh
python3 -m unittest discover -s deploy/beijing/tests -p 'test_*.py' -v
```
