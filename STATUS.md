# 项目状态

- 最后更新：2026-10-15（北京时间）
- 当前阶段：`P3 榫卯 AI 社区新加坡根域备用通道代码就绪，等待服务器首次安装与切换验收`。
- 最新检查点：`SUNMAO_COMMUNITY_0_2_1_SINGAPORE_CHANNEL_LOCAL_READY`。

## 本轮已完成

- 按所有者最新决定冻结独立社区仓库，继续维护本仓库`services/sunmao-ai-community/`；社区版本提升到`0.2.1`，加入`amuyeye.com`精确同源支持，同时明确拒绝`mcp.amuyeye.com`、`www.amuyeye.com`与其他主机。
- 新增ADR-0068及`deploy/singapore/`安全通道：独立社区服务仍使用18808和Git外SQLite；`compassdev`只可运行固定社区发布器；没有Git轮询或timer。Caddy模板只声明`amuyeye.com`和`xn--4pv19va7008a.com`，不含MCP或通配符。
- 新加坡Caddy脚本采用增量站点文件与组合预验证；若时变罗盘仍占用根域则在写入前停止。切换前后强制检查即时AI、`compass.grandpaamu.com`及`mcp.amuyeye.com`，MCP仍须保持OAuth 401挑战。未修改时变罗盘环境或原Caddyfile。
- 本轮21项社区测试通过。此检查点只证明本地代码和安全发布文件就绪；尚无新加坡服务器执行或公网切换证据，不能声称`0.2.1`已上线。

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

- 榫卯AI社区最近经验证的生产版本仍是北京独立`0.2.0`、提交`ed01937e317cdd5142f25e6c6152ff7fa85df64d`；备案拦截使该入口不能作为当前可用性证据。新加坡目标为`https://amuyeye.com/`和`https://阿木爷爷.com/`共同访问社区，但`0.2.1`尚未切换。普通main推送不自动上线；正式分支仍为`sunmao-production`。
- 北京采集器：`1.2.0`、提交`10fdb3a313f8851c8e7963fb29bc2935dfb164ae`；模型下载器最近记录为`0.10.5+git.exact-profile-hd`、提交`bb4d73ba05908040b18611452e7bd72a48a069c6`。本次未改变其版本。
- 即时AI继续使用`https://grandpaamu.com/`；时变罗盘网页已可从`https://compass.grandpaamu.com/`访问；MCP继续使用`https://mcp.amuyeye.com/`。历史锚点`GRANDPAAMU_0_21_1_LIVE`和最近通道检查`CODEX_CLOUD_PUBLISH_READY`继续保留；本轮只读核对这些入口，没有修改或重启对应服务。
- 详细历史（模型先生单条高清替换、原链接恢复、学习标记、播放器、旧稿治理及Web Push）继续保存在CHANGELOG及对应ADR，不在当前快照重复堆积。

## 当前阻塞与待验收

- 新加坡首次安装、根域释放、社区Caddy增量加载、中文域DNS指向和公网端到端验收尚未执行。本地任务没有连接到右侧Workbench终端的受支持输入通道，因此未把“Git已修改”冒充“服务器已上线”。
- 尺寸型的真实资料链接尚待所有者提供；“108道”为合集名称，当前实际收录34集，不宣称已有108条或持续自动同步。
- 社区当前提供公开纯文字交流，不含账号、私信、图片上传或AI问答。默认90天/5000条保留、20/500字长度限制、同源提交及15秒/每小时10条内存限流继续生效。
- 既有即时AI待主人真机验收事项保留：iPhone高清视觉/连续播放、抖音原链接、已学定位/取消、1997年SEC旧稿不再置顶、主屏Web App首次及自然消息通知。服务器和Chromium测试不能替代这些验收。
- 原模型先生视频订阅保持；本人回复事件仍待主人在原长期Work对话追加。ChatGPT事件通知与即时AI财经Web Push独立，本轮未修改通知规则。

## 下一项唯一建议任务

`在新加坡服务器安装固定社区发布通道并完成根域切换验收`：必须保留`mcp.amuyeye.com`与两个并列应用，服务器执行和公网证据齐全后才能把P3-SUNMAO-04改为DONE。
