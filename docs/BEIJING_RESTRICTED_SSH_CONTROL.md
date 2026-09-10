# 北京受限 SSH 控制：首次启用与手机使用

## 2026-09-10 17:48 最新结果（优先于下方历史）

- `BEIJING_SSH_PUBLISH_VERIFIED`：新加坡compassdev通过实际开发main中的客户端完成正式发布，并以独立verify再次核验。accepted/current/deployed/live均为 `d60f75fd98bc5f09aca6e1e70d6827a85db97c2e`，组件tree `d6568c7a3f9662dd8456c7ddaaf21378b3890dea`。北京真实220项隔离测试通过，publisher inactive/success、collector active，timer disabled/inactive。
- 北京17:30第一次fetch因30秒低于1024 bytes/sec退出128；本人恢复可信管理登录后读取该次有限脱敏日志，同账户Git直连预检成功。d60f75f与ff4f42e仅五份结果文档不同，组件/控制代码相同；经现有发布器受控重试一次成功，未修改基础设施或永久Git网络配置。
- 北京正式域名与回环均返回新SHA/1.0.8；新加坡直接HTTPS仍三次reset，DNS正确，独立网络原因未查明。不能声称新加坡HTTPS通过或媒体传输失败，也不要扩大受限SSH权限来处理它。原模型下载器PID959090与路径不变。
- 真实开发入口已整合进main：`/home/compassdev/Documents/Codex/2026-08-26/instant-ai-finance`；bootstrap独立目录仍只作历史恢复。main后续结果文档提交不等于再次上线。
- 手机本人发起且电脑关闭的验收尚未进行；服务器端新Codex CLI任务已通过，但不是实机替代。真实回滚演练未做，模型下载器仍pending_verified_import。

手机新任务可直接发送：

> 请先读取 /home/compassdev/BEIJING-CONTROL-STATUS.md，再进入 /home/compassdev/Documents/Codex/2026-08-26/instant-ai-finance，阅读AGENTS、STATUS和ADR-0046。以compassdev执行python3 -B deploy/beijing/ssh-control/client.py inspect和status，仅核对北京运行版本及timer状态。不要修改、发布或重建密钥。

日后修改并发布的单句口令：

> 更新并发布北京博主采集器：〈具体需求〉。在新加坡真实开发项目按AGENTS及ADR-0046检查、修改services/beijing-blogger-collector、测试并推送main，再运行受限SSH客户端publish完整SHA并验证实际回执。保留既有数据和视频传输，不改模型下载器、新加坡即时AI、时变罗盘或timer。

## 2026-09-10 17:32 历史结果（已由上方成功验收替代）

- main整合已完成：ff4f42eace3f77470395d184ed1c4d33271973be，由新加坡compassdev推送。
- 真正的新加坡Codex CLI新任务只读验收成功，标记SG_CODEX_NEW_TASK_READONLY_OK。手机本人发起/电脑关机实机部分未做，不以CLI结果冒充。
- 新加坡30项控制契约及仅组件Git archive的220项隔离测试通过。测试venv在 /home/compassdev/beijing-release-validation-venv，不在生产或Git内。
- 已按用户明确授权仅触发一次北京发布。beijing-production目标ff4f42e，但publisher failed/exit-code，accepted/current/deployed/live仍7da4421；collector和模型下载器均保持旧进程active，timer disabled/inactive。未上线，不盲目重复publish。北京可信管理登录失效，需恢复登录后查看固定发布服务的脱敏错误；受限账号不增加任意日志/通用sudo权限。
- 当前真实开发入口是 /home/compassdev/Documents/Codex/2026-08-26/instant-ai-finance；原 /home/compassdev/beijing-collector-control 仍固定在bootstrap ecbbdfe，仅作只读恢复，不作为最新发布工作区。

手机在新加坡新任务中可给出：

> 先读取 /home/compassdev/BEIJING-CONTROL-STATUS.md，再进入 /home/compassdev/Documents/Codex/2026-08-26/instant-ai-finance，读取当前STATUS与ADR-0046。以compassdev执行python3 -B deploy/beijing/ssh-control/client.py inspect和status，只读核对北京，暂不重复publish。不要重建密钥，不要读取.env或业务资料。当前有一次发布未完成，先根据错误证据处理。

今后具体功能修改仍限services/beijing-blogger-collector并先测试。正常发布须完整SHA匹配干净HEAD和origin/main；失败回执只用verify查询，不自动重复触发。模型下载器继续pending_verified_import。

状态：**北京受限SSH已安装，新加坡跨区只读与权限边界已实测通过**。
标记：`BEIJING_SSH_READONLY_LIVE_VERIFIED`。这不是已完成业务发布或手机关机电脑验收的凭证。
架构见 ADR-0046。下述16:58节为只读启用历史；所有者随后明确授权完成main整合、独立验证并直接发布北京博主采集器。当前任务不修改timer、服务器发布器、新加坡即时AI生产或模型下载器；完成情况以最新状态/真实回执为准。

## 2026-09-10 16:58 当前已验收状态

- 北京官方Git refs恢复200（约0.65秒），固定提交 `ecbbdfe129c539a3b1ec5361499e7c2d5ebc08a5` 已完整下载，root持有且干净；无代理/VPN/DNS/镜像或永久Git配置变更。此前超时保留为跨区波动证据，不保证未来每次Git下载都成功。
- 北京已执行默认预检与获准的 `--apply`，只安装专用账号和七个控制文件，输出 `BEIJING_READONLY_CLOUD_CONTROL_READY user=beijingcodex`。安装器校验后仅reload现有SSH；再次默认只读预检通过，未覆盖其他账号/文件或重启业务。
- 专用账号uid990/gid989、无附加组，sudo仅两条无参数固定包装器。现场非白名单sudo返回1；新加坡空shell、任意命令、附加参数、通用sudo拒绝64，PTY与TCP转发拒绝255。SFTP初始化拒绝64且协议响应0字节，没有文件访问会话。第一轮要求SFTP文本提示的断言不适用，复核依据是初始化退出及无协议响应，不修改服务权限。
- 新加坡真实compassdev调用inspect/status成功，并再次读取状态核验：accepted/deployed/current/live均为 `7da442128f8aef619c593766a4b62736fb19a4de`；collector active，publisher inactive/result success，timer inactive/disabled。模型下载器仍active。没有真实publish、生产分支推进、业务重启或数据改动。
- 两端固定源码目录、新加坡既有专用密钥和可信主机固定继续复用，禁止重新生成。北京七个控制文件包括 `/usr/local/libexec/beijing-codex-dispatch`、`/usr/local/lib/beijing-codex-control/status.py`、两个sbin包装器、`/etc/beijing-codex-control/authorized_keys`、SSH Match drop-in、专用sudoers；内容/模式由同一安装器精确复核。
- 新加坡 `/home/compassdev/BEIJING-CONTROL-STATUS.md` 已把当前已验收状态放到开头，旧网络阻塞记录保留为历史。手机新任务先读此文件，再执行下面绝对路径的inspect/status即可，不需要本地电脑代发命令。
- 未完成：电脑关闭后手机新任务独立验收；控制分支经审查整合进实际开发main；明确授权后的首次生产publish/回滚验收。原模型下载器真实源码仍未导入。当前独立detached控制目录不能直接发布另一个仓库目录的提交。

## 2026-09-10 16:44 历史检查点（已被上方验收替代）

- 本次所有者明确批准使用已登录内置浏览器 Workbench；这是针对此次两台目标的例外，不是未来任意云资源操作许可。两端 admin 既有 sudo 已核实。
- 北京为 cn-beijing SWAS `e905b859a6c24c529ef5c2ea21658462`，公网 `47.93.214.76`，AccountId `1051070265710978`。新加坡为 ap-southeast-1 SWAS `c9e012a3ed944b588d696e71c0e24aea`，公网 `47.236.175.118`；不是把 hostname 中的旧 i-t4nik 名称当成 ECS InstanceId。
- 已按单独明确确认，仅新增北京 TCP22 来源 `47.236.175.118/32` 的规则，原六条保持不变。新加坡 compassdev（uid1001）直连北京收到 SSH banner，205ms；不能拿此结果代替真实登录验收。
- 新加坡 `/home/compassdev/.config/instant-ai-beijing-control/` 已创建为0700；`ssh_identity` 与 `known_hosts` 为0600、归 compassdev。专用 Ed25519 私钥只在新加坡生成和保留，没有显示、导出或写入 Git。**恢复工作必须复用，禁止重新生成或覆盖。**
- 北京公开 SSH 主机密钥来自北京现有可信终端；已核对公开指纹后固定在新加坡。没有通过不可信扫描自动接受主机，也没有关闭验证。
- 新加坡独立控制源码目录 `/home/compassdev/beijing-collector-control` 已取得干净的固定提交 `ecbbdfe129c539a3b1ec5361499e7c2d5ebc08a5`（detached HEAD）。首次默认 Git 拉取未完成；单次命令使用 HTTP/1.1 / Git protocol v0 后成功，未修改持久代理或 Git 网络配置。
- 原开发仓库是 `/home/compassdev/Documents/Codex/2026-08-26/instant-ai-finance`，检查时 main 为 `20c70e056f17785f40cfe44ed17c82790a292e88` 且干净；`/opt/instant-ai/repository` 和罗盘 workspace 不动。控制分支尚未合并到 main，不能声称原 main 已有新控制命令。
- 北京 `/opt/beijing-codex-bootstrap/repository` 只新建了隔离 Git 目录，首次固定提交 fetch 报低速超时；后续官方 Git 直连兼容查询和现有 bloggergit 只读 ls-remote 也超时。GitHub 首页直连200并不能证明 Git 拉取可用；未配置 VPN、镜像或新代理。
- 北京专用 beijingcodex、七个授权文件尚未安装，安装器预检/应用、SSH 生效验证、越权拒绝与手机脱离电脑验收均未完成。不要运行 publish 代替连通测试。
- 2026-09-10T08:44:15Z 最终复核：beijingcodex 不存在，生产 ref/current 链接仍为 `7da442128f8aef619c593766a4b62736fb19a4de`；collector active，publisher inactive/result success，timer inactive/disabled。Git endpoint 后续 TCP443 连接也超时，不能把某次首页200当成持续可用。
- 新加坡 Linux22项控制隔离测试全部通过，不是SSH现场验收。另已在新加坡 Git外新建 `/home/compassdev/BEIJING-CONTROL-STATUS.md`（0600），手机新任务可读取恢复；本地文档不作为唯一进度来源。

恢复顺序：先诊断北京官方 Git 下载路径，取得并验证同一个固定 SHA 的 root 持有干净源码，再传入新加坡既有 `.pub` 公钥，运行预检和获准的 `--apply`，最后由 compassdev 使用下列固定客户端读取与拒绝越权验证。任何新代理、镜像、中转服务或额外防火墙变更都不在本轮许可内。

## 一次性启用闸门

1. 在可信 Alibaba Cloud Client 确认北京轻量实例
   `e905b859a6c24c529ef5c2ea21658462 / 47.93.214.76` 及所属账号。
   另确认新加坡 SWAS `c9e012a3ed944b588d696e71c0e24aea`。账号未核实时不能代填。
2. 由**新加坡 Codex**只读确认真实 Git 根/远程/工作区、当前用户、出口与到北京 22 端口的
   连接；由北京既有 root 终端确认 sshd 与固定发布器。Windows 的成功或失败不作跨区证据。
   不需要 RAM 角色，不读取云客户端配置、令牌或 .env。默认使用客户端；本次内置浏览器例外及精确来源规则另见上方现场授权记录。
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

当前只读控制源码已固定在独立目录且已通过现场验收，由新加坡 compassdev 执行（不是 Windows，也不是罗盘工作区）：

```sh
python3 /home/compassdev/beijing-collector-control/deploy/beijing/ssh-control/client.py inspect
python3 /home/compassdev/beijing-collector-control/deploy/beijing/ssh-control/client.py status
```

`inspect` 输出服务与 Git 元数据；`status` 输出版本/状态白名单，不读取日志、Cookie、
环境变量配置或业务正文。新加坡专用密钥/主机公钥不存在或权限不安全时直接停止。

控制源码已整合到真实开发main；正式使用发布客户端时须保持客户端所在Git根、干净HEAD与origin/main一致。不能拿旧detached bootstrap目录直接发布其他目录里的提交。

收到主人**明确发布指令**、完成需求/测试/审查并提交到 main 后，才在该真实开发工作区运行：

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
- 已完成专用密钥、可信主机固定、北京授权安装、新加坡真实SSH只读及越权拒绝检查、main整合、新Codex CLI任务和真实生产发布；未做电脑关机手机验收与回滚演练。新加坡到北京HTTPS reset另列待查，不影响已经完成的SSH回执验收。

本地验证命令（只用合成数据）：

```sh
python3 -m unittest discover -s deploy/beijing/tests -p 'test_*.py' -v
```
