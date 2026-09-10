# ADR-0046：手机经新加坡 Codex 维护北京的受限 SSH 通道

- 状态：ACCEPTED / IMPLEMENTATION_NOT_ACTIVATED
- 日期：2026-09-10
- 授权：所有者批准最终方案后要求“开始实施”。不是生产业务发布授权。
- 替代范围：ADR-0045 的当前北京控制传输选择；双组件隔离、源码核验、数据保护继续有效。

## 事实与原因

可信 Alibaba Cloud Client 显示，北京 `47.93.214.76` 是轻量应用服务器（SWAS），
并非 ECS。实例为 `e905b859a6c24c529ef5c2ea21658462`；北京账号 ID 尚未核实。
不能把新加坡账号 ID 填入北京策略，也不能把 ECS 的 `CommandRunAs` 约束直接套到 SWAS。
既有 CLI、模板、Git 发布器和只读桥保留，不以扩大 RAM 权限解决产品差异。

北京到新加坡的现有媒体通道是独立签名 HTTPS/outbox/回执，所有者确认正在正常工作。
其密钥不能变成远程命令凭据，传输接口不得执行 shell，也不新增后台控制轮询。

## 决定

1. 日常入口为手机上**新加坡实例**的 Codex 项目；该项目指向真实即时 AI Git 工作区。
   北京代码仍位于 `services/beijing-blogger-collector/`，不是时变罗盘项目。
2. 新加坡持有专用、Git 外 SSH 私钥，北京只安装公钥。主机密钥必须由既有可信北京
   root 连接取得并核对，禁止盲信 `ssh-keyscan`、关闭主机检查或使用桌面 SSH agent。
3. 仅连接北京固定 IP、既有 22 端口。来源限制固定为已确认的新加坡出口 IP；如果实际
   出口不是 `47.236.175.118`、端口不通或主机密钥不同，停止报告，不开放安全组或代理。
4. 北京专用用户 `beijingcodex`、锁定密码、root 持有家目录和授权文件；只接收
   `inspect / status / publish` 三个完整字符串。任意 shell、附加参数、SFTP、PTY、
   TCP/agent/X11 转发均不开放。`status` 不提权，只读固定部署元数据和回环版本。
5. sudo 仍只有两个 root 包装器，并以 `""` 限定无参数：盘点、触发现有一次性发布。
   既有 root 发布器自身、业务服务单元和媒体通道不被本次 Git 修改替换。
6. 首次安装必须经可信 Alibaba Cloud Client 的北京 root 连接。安装器默认仅检查；
   `--apply` 才安装。已有不同用户/授权/文件停止，不覆盖。只增加控制文件、专用账户，
   验证 SSH 生效配置后 reload 现有 SSH 服务；验证 root/其他用户配置不变，不重启业务。
7. 用户只要求改代码时不发布。明确要求发布后，才从干净、与 `origin/main` 完全一致的
   工作区将完整 SHA 安全快进到 `beijing-production`，一次触发并查询回执。禁止 force、
   自动重试触发、自动推进 main 最新业务、修改任意 timer。
8. 验收区分 accepted repository revision 与 deployed component revision/tree。
   组件 tree 未变时不要求无意义重启；但回环实际运行 revision、当前链接和部署账本必须
   一致，服务健康、一次性发布服务成功退出、timer disabled/inactive 才算成功。
9. 人工回滚仍是经过测试的向前 revert 提交；新版本健康失败沿用既有自动恢复上一 release。
   本轮不真实触发发布或回滚演练。单主人仍须避免两个同时推进生产分支的任务。

## 验收与边界

- 本地隔离测试与真实服务启用分开记账。文件存在、单测通过、安装完成都不等于 SSH 已通。
- 从新加坡实际执行握手、只读盘点、状态、越权拒绝；不能用 Windows/VPN 的结果代替。
- 使用过程不依赖桌面代理；`ProxyCommand/ProxyJump/IdentityAgent` 全部禁用。
  这不代表关闭了服务器 OS 级隧道，也不保证手机访问 Codex 不需要自己的网络条件。
- 最终在电脑关机后，由手机新任务完成上述检查及另行授权的实际版本/回滚验收。
- 原模型下载器继续 `pending_verified_import`。未核实真实源码，不冒用桥接器或博主代码。
- 若 SSH 不可用，只报告网络/授权所在层；HTTPS 控制任务拉取为后备设计，须另行审查和授权。

## 上游依据

- OpenSSH authorized_keys 限制：[sshd(8)](https://man.openbsd.org/sshd.8#AUTHORIZED_KEYS_FILE_FORMAT)
- Match/ForceCommand/DisableForwarding：[sshd_config(5)](https://man.openbsd.org/sshd_config.5)
- SWAS 权限与 ECS 不同：[官方 RAM 文档](https://www.alibabacloud.com/help/zh/simple-application-server/developer-reference/api-swas-open-2020-06-01-ram)
