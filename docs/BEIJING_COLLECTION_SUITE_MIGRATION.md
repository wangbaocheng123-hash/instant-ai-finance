# 北京采集套件统一迁移手册

> 2026-09-10 更正：当前 `47.93.214.76` 是 SWAS 轻量应用服务器，不是 ECS。
> 下列旧阶段 A 的 ECS RAM 步骤不能用于该目标。当前受控入口改按 ADR-0046 和
> `BEIJING_RESTRICTED_SSH_CONTROL.md` 实施；仍未现场启用。其余数据隔离与源码核验闸门保留。

本手册执行 ADR-0045。目标是以后只维护一个公共 Git 仓库、一个
`beijing-production` 和一套北京发布流程；模型下载器与博主采集器仍保持两个服务、
两套数据和独立回滚。

## 当前事实

- 已纳管：`services/beijing-blogger-collector/`、只读 `ModelDownloaderBridge`、既有
  `blogger-collector-git-deploy.service`。
- 尚未纳管：现场原模型下载器真实源码、构建依赖、systemd unit 和发布器。
- 当前生产分支只会发布博主采集器，不能修改模型下载器。
- 当前新加坡主机已安装官方 Alibaba Cloud CLI 3.4.11，但没有 RAM 实例角色；CLI 内
  不保存 AccessKey。
- `deploy/beijing/collection-suite.json` 是过渡清单；其中模型下载器必须维持
  `pending_verified_import`，统一发布 timer 必须维持关闭，直至本手册闸门全部通过。

## 阶段 A：一次性建立受控通道

1. 在可信阿里云控制面查明当前新加坡 ECS 的实例 ID、账号 ID和现有实例角色。一个 ECS
   只能绑定一个实例角色；若已存在角色，向原角色追加本项目的窄权限，不能直接替换。
2. 复制 `deploy/beijing/ram-cloud-assistant-policy.example.json`，只替换
   `<ACCOUNT_ID>` 与 `<BEIJING_INSTANCE_ID>`。策略不得扩展到其他实例或其他 ECS 写操作。
3. 创建/复用一个 ECS 服务角色，挂载上述策略，并只把该角色绑定到当前新加坡 ECS。
   不创建 AccessKey，不把 STS 响应、CLI 配置或任何凭据提交 Git。
4. 通过既有可信北京维护入口，以 root 从本仓库已核验提交运行：

   ```bash
   deploy/beijing/install-readonly-cloud-control.sh
   ```

   它只创建锁定密码的 `beijingcodex`、安装两个 root 持有的固定包装器和精确 sudoers；
   不修改模型下载器、博主配置、数据库、媒体、Caddy、采集开关或定时器。
5. 在新加坡主机使用 `EcsRamRole` 模式配置 CLI，区域固定为 `cn-beijing`。先验证身份和
   Cloud Assistant 状态，再通过 `RunCommand` 以 `Username=beijingcodex` 执行：

   ```text
   sudo -n /usr/local/sbin/beijing-suite-inspect
   ```

   实际结果必须再由 `DescribeInvocationResults` 核对，不能把“命令已发送”当成成功。

## 阶段 B：只读盘点与源码接入

1. 保存盘点器输出中的 service 状态、工作目录、Git 根、branch、revision、文件属主和
   权限；不读取 environment、Cookie、数据库正文、评论正文或命令行参数。
2. 若模型下载器已有独立 Git，记录远程身份和精确提交，但不得导入凭据或改写原历史；
   若只有现场源码，先计算只读清单与摘要，再生成不含运行数据的受控快照。
3. 对候选快照逐项排除：`.env*`、Cookie、账号/密码、API Key、SQLite、媒体、评论原始
   JSON、浏览器 profile、日志、缓存、备份、绝对生产路径和生成物。
4. 把核验后的代码放入 `services/beijing-model-downloader/`，保留许可证与来源说明；
   先补齐本地/隔离测试，再把中央清单状态改为 `managed`。没有真实源码时必须停在本步，
   不得用 `mx_agent` 或桥接 schema 仿造。

## 阶段 C：统一发布但保持组件隔离

统一发布器必须完成以下顺序：

1. 只读取 `beijing-production`，校验 fast-forward、普通文件、安全路径和敏感文件黑名单。
2. 计算两个组件各自的 Git tree；tree 未变的组件不构建、不重启。
3. 在无生产凭据和受限网络环境分别构建、测试两个新 release。
4. 分别原子切换组件链接并核对各自回环健康；任一失败只回滚失败组件。
5. 写入同一个 suite revision 和两个 component revision；不触碰 Git 外数据。
6. 连续两个新提交完成上述流程后，才启用一个 90 秒代码检查 timer。该 timer 只发布
   Git 变化，永远不承担评论抓取或其他业务调度。

## 阶段 D：实现模型先生评论刷新

在已导入的真实模型下载器中实施 ADR-0044：抖音实际发布时间未满 24 小时每 15 分钟，
满 24 小时每 60 分钟；按上次成功时间计算，失败不推进游标，同作品不并发，Chromium
全局串行，重启后恢复超期任务。评论合并保留完整回复关系和作者点赞
`true/false/null`；点赞或取消点赞均提高 revision，并及时交给既有桥/outbox。

## 最终验收

- `main` 与 `beijing-production` 关系可安全快进，工作区干净，无敏感文件。
- 两组件完整测试、shell 语法、静态检查和公开健康检查通过。
- 15/60 分钟边界用合成时钟验证；一次经所有者批准的真实新作品观察确认调度与传输。
- 手机即时 AI 显示粉丝原问题、模型先生回复和作者点赞状态；不触发额外 ASR/豆包任务。
- 停止任一北京 service 后另一项仍健康；分别执行一次失败回滚演练。
- RAM 越权负例通过：其他实例、其他用户名、ECS 启停/删除/网络操作均被拒绝。

完成全部验收前，旧发布器、旧 release 和全部业务资料必须保留，生产不得宣称已经统一。
