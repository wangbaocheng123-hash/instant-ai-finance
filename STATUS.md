# 项目状态

- 最后更新：2026-10-13 14:20（北京时间）
- 当前阶段：`P3 榫卯 AI 社区首次独立迁移及第二阶段正式上线完成`。
- 最新检查点：`SUNMAO_COMMUNITY_0_2_0_INDEPENDENT_PUBLIC_LIVE`。

## 本轮已完成

- 社区已独立上线至`https://阿木爷爷.com/`（`https://xn--4pv19va7008a.com/`），公网及回环版本均为`sunmao-ai-community` / `0.2.0` / `ed01937e317cdd5142f25e6c6152ff7fa85df64d`，部署时间`2026-10-13T06:12:44Z`。
- 代码仍在现有仓库的`services/sunmao-ai-community/`；独立使用`sunmao-production`、`sunmao-ai-community.service`、`127.0.0.1:18808`、`/opt/sunmao-community/current`及Git外`/var/lib/sunmao-community/community.sqlite3`。并列关系及发布证据见ADR-0067。
- 按所有者对本次明确对象的一次性例外授权，通过右侧北京轻量服务器命令助手完成固定发布器/systemd安装、正常Git发布及Caddy切换。例外随本任务结束，后续阿里云控制面恢复AGENTS规定的Alibaba Cloud Client默认入口；不扩展为持续网页控制权限。
- 首次发布修复了Bash局部变量初始化顺序错误并加入回归；GitHub网络不稳定期间，通过GitHub blob接口补齐两个缺失对象并逐一核对Git对象摘要。最终由正常固定发布器完成发布，不使用临时应用源码替换或旁路发布脚本。
- Caddy只切换社区域名至18808，备份为`/etc/caddy/backups/Caddyfile.20261013T061446Z`；校验、reload及公网HTTPS验收成功。社区、采集器、模型下载器、Caddy均为`active`。切换和社区重启验收期间，两个并列应用的InvocationID均未变化。
- 首页默认交流区；底部左/中/右分别为尺寸型、交流区、榫卯108道。尺寸型预留器物尺寸、结构尺寸、图纸连接；34条原始视频标题、封面来源及抖音链接完整保留，不托管视频。
- 公网发言返回201，立即重复发送返回429及`Retry-After: 15`；社区真实重启后测试留言仍在。验收后仅将该条合成测试留言标记隐藏，可恢复，未删除或修改用户留言。
- 本地16项社区测试通过；线上三入口切换、34个新窗口原视频链接、首末作品ID及手机窄屏无横向溢出通过；浏览器无已捕获的错误/警告。未冒充iPhone真机验收。
- `beijing-production`保持`10fdb3a313f8851c8e7963fb29bc2935dfb164ae`，采集器仍为`1.2.0`；未发布或重启采集器、下载器。采集器Git timer为`disabled/inactive`，社区Git timer未安装（`not-found`），无Git定时轮询。

## 当前生产基线

- 榫卯AI社区：`https://阿木爷爷.com/`，独立`0.2.0`、提交`ed01937e317cdd5142f25e6c6152ff7fa85df64d`，正式分支`sunmao-production`。普通main推送不自动上线；社区发布短语仍为“更新并发布榫卯AI社区：<具体需求>”。首版锚点`SUNMAO_COMMUNITY_34_ITEM_PUBLIC_LIVE`保留为历史。
- 北京采集器：`1.2.0`、提交`10fdb3a313f8851c8e7963fb29bc2935dfb164ae`；模型下载器最近记录为`0.10.5+git.exact-profile-hd`、提交`bb4d73ba05908040b18611452e7bd72a48a069c6`。本次未改变其版本。
- 即时AI：`https://grandpaamu.com/`，最近已验收版本`0.28.2`、提交`3668b6cc962d9ede861214362a2f88a7ecb504f9`；本次未操作新加坡服务。历史锚点`GRANDPAAMU_0_21_1_LIVE`及最近通道检查`CODEX_CLOUD_PUBLISH_READY`保留，非本轮重新探测。
- 详细历史（模型先生单条高清替换、原链接恢复、学习标记、播放器、旧稿治理及Web Push）继续保存在CHANGELOG及对应ADR，不在当前快照重复堆积。

## 当前阻塞与待验收

- 本次社区部署无阻塞。尺寸型的真实资料链接尚待所有者提供；“108道”为合集名称，当前实际收录34集，不宣称已有108条或持续自动同步。
- 社区当前提供公开纯文字交流，不含账号、私信、图片上传或AI问答。默认90天/5000条保留、20/500字长度限制、同源提交及15秒/每小时10条内存限流继续生效。
- 既有即时AI待主人真机验收事项保留：iPhone高清视觉/连续播放、抖音原链接、已学定位/取消、1997年SEC旧稿不再置顶、主屏Web App首次及自然消息通知。服务器和Chromium测试不能替代这些验收。
- 原模型先生视频订阅保持；本人回复事件仍待主人在原长期Work对话追加。ChatGPT事件通知与即时AI财经Web Push独立，本轮未修改通知规则。

## 下一项唯一建议任务

`接收并整理第一批工艺品尺寸链接`：待所有者提供资料后填入尺寸型入口，普通开发先提交main；正式上线仍按社区独立发布约定执行。
