# 项目状态

- 最后更新：2026-10-15（北京时间）
- 当前阶段：`P3 榫卯 AI 社区 0.3.2 功能已回并原仓库，等待北京发布源安全回切与生产验收`。
- 最新检查点：`SUNMAO_COMMUNITY_0_3_2_MONOREPO_REJOIN_LOCAL_READY`。

## 本轮已完成

- 已准备`amuyeye.com`根域名与`阿木爷爷.com`共用北京社区服务的精确Caddy配置：只声明两个根域名并代理18808，明确排除`mcp.amuyeye.com`、`www`与通配符。应用本身原已允许两个精确Host；当前尚未切换DNS或声称英文域名上线，必须先应用北京Caddy、再把`@`记录切至北京并完成HTTPS验收。
- 按所有者最终确认，社区日常源码回到本仓库`services/sunmao-ai-community/`；独立仓库只保留为只读迁移历史。撤销未执行的新加坡社区部署方向，社区仍在北京以18808、独立systemd和Git外SQLite与采集器并列运行。
- 把独立仓库0.3.2的产品能力回并：第一方匿名访问统计、`/admin/`密码后台、右上角刷新按钮及密码摘要生成工具。继续支持精确`amuyeye.com`和`xn--4pv19va7008a.com`同源，拒绝`mcp.amuyeye.com`与`www`；不回并独立仓库Webhook发布逻辑。
- 新增ADR-0068和`rejoin-original-repository.sh`。脚本先验证社区与采集器健康，把独立裸仓库和修订状态移入可恢复备份，恢复本仓库`sunmao-production`子树发布器，并关闭Webhook path及所有Git timer；它不修改当前release、数据库、后台密码摘要或Caddy，不重启任何服务。
- 24项社区测试完整通过。当前只完成本地代码、文档和回切工具验收；尚未操作北京服务器、推进`sunmao-production`或验证公网，因此不能声称本仓库0.3.2已上线。

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

- 独立仓库记录的最近精确线上验收为`0.3.2`、提交`28276ad681a477fb0e71e44a1ebd5c20b6d1ffe4`；当前北京发布源仍需实时核对后才能回切。本仓库生产基线仍为`sunmao-production`的`ed01937e317cdd5142f25e6c6152ff7fa85df64d`。普通`main`推送不自动上线，回切与功能发布必须分步验收。
- 北京采集器：`1.2.0`、提交`10fdb3a313f8851c8e7963fb29bc2935dfb164ae`；模型下载器最近记录为`0.10.5+git.exact-profile-hd`、提交`bb4d73ba05908040b18611452e7bd72a48a069c6`。本次未改变其版本。
- 即时AI继续使用`https://grandpaamu.com/`；时变罗盘网页已可从`https://compass.grandpaamu.com/`访问；MCP继续使用`https://mcp.amuyeye.com/`。历史锚点`GRANDPAAMU_0_21_1_LIVE`和最近通道检查`CODEX_CLOUD_PUBLISH_READY`继续保留；本轮只读核对这些入口，没有修改或重启对应服务。
- 详细历史（模型先生单条高清替换、原链接恢复、学习标记、播放器、旧稿治理及Web Push）继续保存在CHANGELOG及对应ADR，不在当前快照重复堆积。

## 当前阻塞与待验收

- 北京发布源回切尚未执行：需先从新加坡既有`/home/compassdev/bin/beijing-admin`核对实时源与服务，再运行一次性回切脚本；其后才可推进`sunmao-production`并只启动一次社区发布器。备案与外网域名拦截单独处理，不用新加坡另部署社区来绕过。
- 尺寸型的真实资料链接尚待所有者提供；“108道”为合集名称，当前实际收录34集，不宣称已有108条或持续自动同步。
- 社区当前提供公开纯文字交流，不含账号、私信、图片上传或AI问答。默认90天/5000条保留、20/500字长度限制、同源提交及15秒/每小时10条内存限流继续生效。
- 既有即时AI待主人真机验收事项保留：iPhone高清视觉/连续播放、抖音原链接、已学定位/取消、1997年SEC旧稿不再置顶、主屏Web App首次及自然消息通知。服务器和Chromium测试不能替代这些验收。
- 原模型先生视频订阅保持；本人回复事件仍待主人在原长期Work对话追加。ChatGPT事件通知与即时AI财经Web Push独立，本轮未修改通知规则。

## 下一项唯一建议任务

`把北京社区发布源安全恢复到原仓库并上线0.3.2回并版本`：必须先保存独立源备份，再经`sunmao-production`和社区专用发布器上线；核对`/health/version`、后台和统计，同时证明采集器与模型下载器未重启后才能把P3-SUNMAO-04改为DONE。
