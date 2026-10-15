# 项目状态

- 最后更新：2026-10-15 11:55（北京时间）
- 当前阶段：`P3 榫卯 AI 社区 0.3.2 已回切原仓库并完成北京生产发布`。
- 最新检查点：`SUNMAO_COMMUNITY_0_3_2_MONOREPO_REJOIN_BEIJING_LIVE`。

## 本轮已完成

- 已准备`amuyeye.com`根域名与`阿木爷爷.com`共用北京社区服务的精确Caddy配置：只声明两个根域名并代理18808，明确排除`mcp.amuyeye.com`、`www`与通配符。应用本身原已允许两个精确Host；当前尚未切换DNS或声称英文域名上线，必须先应用北京Caddy、再把`@`记录切至北京并完成HTTPS验收。
- 北京实时核对确认回切前社区源为独立仓库、线上release为`babf03b409b6bb3bbfbef85e41abfde4fd95062f` / `0.3.2`，Webhook path处于失败重触发状态；采集器、下载器和社区本身均健康。一次性回切把旧裸仓库及修订状态保存到`/opt/sunmao-community/migration-backups/20261015T035313Z`，备份Git连通性检查通过。
- 社区发布源现精确为`https://github.com/wangbaocheng123-hash/instant-ai-finance.git`。独立Webhook path已`disabled/inactive`，社区Git timer保持未安装/停止，采集器Git timer继续`disabled/inactive`；回切阶段当前release及三个业务进程InvocationID均未变化。
- `sunmao-production`从`ed01937e317cdd5142f25e6c6152ff7fa85df64d`安全快进至`b5ceea0cc4590892a37ab7db38dfbefb39a28265`，只启动一次`sunmao-community-git-deploy.service`。发布器隔离测试、原子切换和健康检查成功，`Result=success`、`ExecMainStatus=0`；社区回环及北京Caddy精确SNI路由均返回`0.3.2`和目标提交，部署时间`2026-10-15T03:55:00Z`。
- `/admin/`返回200，后台摘要文件继续为`sunmaocommunity:sunmaocommunity 0600`，未登录会话回报`configured=true`且统计接口返回401。匿名统计表已在原Git外SQLite中就绪，只保存摘要访客标识、页面、来源域和设备分类，不含IP或完整User-Agent。
- 本地24项社区测试全部通过；生产发布器再次运行同一隔离套件。社区InvocationID按预期从`4f9ba06b42a841d1af8b5c5cf7299ee1`变为`887025b7a4924b2ca84262e14b70496d`。采集器InvocationID仍为`1fe57d8cdcb744b5a1d9c934b1867a27`，下载器仍为`da82cc9fa476410ca7cc535d6e3a7127`，两者版本、进程和生产分支均未发布或重启。

## 北京历史上线基线

- 社区已独立上线至`https://阿木爷爷.com/`（`https://xn--4pv19va7008a.com/`），公网及回环版本均为`sunmao-ai-community` / `0.2.0` / `ed01937e317cdd5142f25e6c6152ff7fa85df64d`，部署时间`2026-10-13T06:12:44Z`。
- 代码仍在现有仓库的`services/sunmao-ai-community/`；独立使用`sunmao-production`、`sunmao-ai-community.service`、`127.0.0.1:18808`、`/opt/sunmao-community/current`及Git外`/var/lib/sunmao-community/community.sqlite3`。并列关系及发布证据见ADR-0067。
- 按所有者对本次明确对象的一次性例外授权，通过右侧北京轻量服务器命令助手完成固定发布器/systemd安装、正常Git发布及Caddy切换。例外随本任务结束，后续阿里云控制面恢复AGENTS规定的Alibaba Cloud Client默认入口；不扩展为持续网页控制权限。
- 首次发布修复了Bash局部变量初始化顺序错误并加入回归；GitHub网络不稳定期间，通过GitHub blob接口补齐两个缺失对象并逐一核对Git对象摘要。最终由正常固定发布器完成发布，不使用临时应用源码替换或旁路发布脚本。
- Caddy只切换社区域名至18808，备份为`/etc/caddy/backups/Caddyfile.20261013T061446Z`；校验、reload及公网HTTPS验收成功。社区、采集器、模型下载器、Caddy均为`active`。切换和社区重启验收期间，两个并列应用的InvocationID均未变化。
- 首页默认交流区；底部左/中/右分别为尺寸型、交流区、榫卯108道。尺寸型预留器物尺寸、结构尺寸、图纸连接；34条原始视频标题、封面来源及抖音链接完整保留，不托管视频。
- 公网发言返回201，立即重复发送返回429及`Retry-After: 15`；社区真实重启后测试留言仍在。验收后仅将该条合成测试留言标记隐藏，可恢复，未删除或修改用户留言。
- 本地16项社区测试通过；线上三入口切换、34个新窗口原视频链接、首末作品ID及手机窄屏无横向溢出通过；浏览器无已捕获的错误/警告。未冒充iPhone真机验收。
- `beijing-production`保持`10fdb3a313f8851c8e7963fb29bc2935dfb164ae`，采集器仍为`1.2.0`；未发布或重启采集器、下载器。采集器Git timer为`disabled/inactive`，社区Git timer未安装（`not-found`），无Git定时轮询。

## 当前生产与目标基线

- 榫卯AI社区当前北京生产为`0.3.2`、提交`b5ceea0cc4590892a37ab7db38dfbefb39a28265`，正式分支为本仓库`sunmao-production`，发布源为原`instant-ai-finance`仓库。普通`main`推送仍不自动上线；后续社区发布继续使用明确发布短语、社区生产分支和一次性发布器。
- 北京采集器：`1.2.0`、提交`10fdb3a313f8851c8e7963fb29bc2935dfb164ae`；模型下载器最近记录为`0.10.5+git.exact-profile-hd`、提交`bb4d73ba05908040b18611452e7bd72a48a069c6`。本次未改变其版本。
- 即时AI继续使用`https://grandpaamu.com/`；时变罗盘网页已可从`https://compass.grandpaamu.com/`访问；MCP继续使用`https://mcp.amuyeye.com/`。历史锚点`GRANDPAAMU_0_21_1_LIVE`和最近通道检查`CODEX_CLOUD_PUBLISH_READY`继续保留；本轮只读核对这些入口，没有修改或重启对应服务。
- 详细历史（模型先生单条高清替换、原链接恢复、学习标记、播放器、旧稿治理及Web Push）继续保存在CHANGELOG及对应ADR，不在当前快照重复堆积。

## 当前阻塞与待验收

- 北京发布源回切与应用发布已完成，无应用发布阻塞。备案、外部DNS与域名可用性仍是独立基础设施事项：本轮没有修改DNS或Caddy；北京本机Caddy的`xn--4pv19va7008a.com`精确SNI路由已返回目标版本，但当前Codex网络无法解析该公网域名，因此不把本轮证据表述为外部互联网或手机真机验收。
- 尺寸型的真实资料链接尚待所有者提供；“108道”为合集名称，当前实际收录34集，不宣称已有108条或持续自动同步。
- 社区当前提供公开纯文字交流，不含账号、私信、图片上传或AI问答。默认90天/5000条保留、20/500字长度限制、同源提交及15秒/每小时10条内存限流继续生效。
- 既有即时AI待主人真机验收事项保留：iPhone高清视觉/连续播放、抖音原链接、已学定位/取消、1997年SEC旧稿不再置顶、主屏Web App首次及自然消息通知。服务器和Chromium测试不能替代这些验收。
- 原模型先生视频订阅保持；本人回复事件仍待主人在原长期Work对话追加。ChatGPT事件通知与即时AI财经Web Push独立，本轮未修改通知规则。

## 下一项唯一建议任务

`在备案与DNS条件具备后补做社区公网和手机真机复验`：只核对社区两个精确域名、后台登录与三入口，不借机修改采集器、模型下载器、即时AI、时变罗盘或Caddy其他站点。
