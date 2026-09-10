# ADR-0045：北京采集套件统一 Git 管理与最小权限云助手控制

- 状态：`ACCEPTED / TRANSITION_IN_PROGRESS`
- 日期：2026-09-10
- 决策者：产品所有者

## 背景

北京服务器现在有两个独立运行的业务组件：原模型下载器，以及后来加入的
`blogger-collector`。两者共享一个主人入口并存在功能协作，但只有
`blogger-collector` 的公开安全快照、测试和发布器已经进入
`instant-ai-finance`。原模型下载器的真实抓取源码、systemd 单元和发布边界尚未进入
公共仓库。现有新加坡云端 Codex 也没有北京实例的受控执行通道，不能安全盘点、导入或
发布原模型下载器。

所有者要求以后从一个仓库、一个生产分支和一套发布流程管理北京采集能力，同时保持
两项服务故障隔离、数据隔离，避免为了“合并”而重写已稳定运行的系统。

## 决定

1. 目标形态命名为“北京采集套件”。它只使用当前公共仓库
   `wangbaocheng123-hash/instant-ai-finance` 和唯一生产分支
   `beijing-production`。中央清单为 `deploy/beijing/collection-suite.json`。
2. 模型下载器与博主采集器仍是两个独立组件：独立 systemd service、进程、配置、
   数据库、媒体目录、日志、资源限制和回滚单元。不得合成一个 Python 进程，不得共用
   SQLite，不得移动、覆盖或重建现有业务数据。
3. 既有 `ModelDownloaderBridge` 继续归博主采集器所有，并以只读方式读取模型下载器
   已完成资料。北京到新加坡仍复用现有签名 HTTPS、HMAC、nonce、摘要、outbox、幂等、
   退避和回执，不新建公网端口、第二个传输 Token 或第二条 MCP。
4. 原模型下载器只有在受控只读盘点完成后才能导入
   `services/beijing-model-downloader/`。导入必须来自现场实际运行源码或其可审计 Git
   源，先排除 `.env`、Cookie、数据库、媒体、浏览器资料、日志、缓存、备份、密钥、
   绝对路径和原始业务 JSON，再补齐测试。不得以仓库中早期 `mx_agent` 示例或桥接器
   反推、仿造生产源码。
5. 统一发布器的最终输入是同一个 `beijing-production` 提交，输出是一个 suite
   revision 和两个 component revision。它必须先在无生产凭据的隔离环境分别检查两项
   组件，然后只切换 Git tree 实际变化的组件；一项失败不得破坏另一项当前正常 release，
   且不得删除任何 Git 外资料。
6. 完成迁移并通过两次连续发布验收后，可以启用一个 90 秒代码发布检查 timer。它只
   获取 `beijing-production` 和比较 tree，不执行抖音采集、Chromium、ASR、豆包、AI 或
   业务定时任务；无变化立即退出，失败 revision 在出现新提交前不重复构建。迁移完成前
   `timer_enabled` 必须保持 `false`，现有 ADR-0037 的
   `blogger-collector-git-deploy.timer disabled/inactive` 规则继续生效。
7. 新加坡云端 Codex 使用 ECS 实例 RAM 角色取得自动轮换的 STS 临时凭据，不保存
   AccessKey。角色只允许对指定北京实例执行 Cloud Assistant `RunCommand`，并把
   `ecs:CommandRunAs` 固定为无登录密码的 `beijingcodex`；查询权限只包含云助手状态与
   本次执行结果。不得授予 ECS 启停、删除、磁盘、安全组、网络、RAM 管理或通配写权限。
8. 北京 `beijingcodex` 没有通用 sudo。过渡期只允许调用两个 root 持有的固定包装器：
   安全元数据盘点，以及触发既有一次性博主采集器发布服务。后续统一发布包装器必须在
   代码审查和生产迁移时单独替换，不能把任意 shell、编辑器或 `systemctl` 通配加入
   sudoers。
9. 模型先生 15/60 分钟评论刷新继续遵守 ADR-0044。它属于模型下载器业务调度，不得
   混入 90 秒 Git 发布检查，也不得在真实源码导入前声称已经实现。

## 过渡状态

- `blogger-collector` 已由当前仓库和既有 `beijing-production` 管理。
- `model-downloader` 在中央清单中标记为 `pending_verified_import`；目标源码目录尚不存在。
- 统一 timer 保持关闭；北京生产行为没有因本 ADR 自动改变。
- 当前仓库提供只读盘点器、北京低权限包装器和 RAM 策略模板。RAM 角色绑定与北京首次
  安装是一次外部控制面动作，完成前云端 Codex不能假装已经取得北京权限。

## 验收闸门

1. RAM 策略中的账户号和北京实例 ID 均替换为真实精确值；新加坡实例只绑定一个已审计
   角色；IMDS 返回角色名，CLI `sts get-caller-identity` 显示 assumed role。
2. 云助手只能以 `beijingcodex` 调用指定北京实例；对其他实例、其他用户名或未列出的
   ECS 写操作明确拒绝。
3. 固定盘点器只返回 unit 状态、工作目录、Git revision 和已知路径的类型/属主/权限，
   不输出 environment、命令行、配置正文、评论正文、Cookie、数据库内容或密钥。
4. 原模型下载器源码导入后，来源提交/现场摘要、排除清单、构建方式、测试、服务 unit、
   数据路径和回滚方式都有书面记录。
5. 两项服务可分别停止、发布和回滚；任一项失败时另一项及两套数据保持不变。
6. ADR-0044 的时间边界、成功游标、失败重试、串行 Chromium、作者点赞三态和即时传输
   回归全部通过。
7. 统一发布器完成两个连续新提交验收后，才允许把中央清单的 timer 改为启用并废止
   ADR-0037 的旧单组件触发规则。

## 回滚

迁移完成前保持现有北京生产不变。迁移后仍通过 `main` 上新的 revert 提交向前推进
`beijing-production`，禁止强推、reset 或回退分支。组件切换失败恢复其上一个正常 release；
RAM 通道可通过从新加坡实例解绑角色立即撤销，不需要轮换任何长期 AccessKey。
