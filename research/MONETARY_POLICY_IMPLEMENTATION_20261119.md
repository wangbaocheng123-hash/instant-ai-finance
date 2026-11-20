# 利率政策线索与罗盘交接验收

日期：2026-11-19。即时AI开发基线`247175b`，罗盘开发基线`368ef03`。本轮为开发候选，不是正式发布或手机通知验收。罗盘配套代码检查点为`f19183d6e12243a51fee30e24ee70e85af7909b9`。

## 来源实抓

美联储官方RSS目录的Monetary Policy入口明确指向`https://www.federalreserve.gov/feeds/press_monetary.xml`，目录原址`https://www.federalreserve.gov/feeds/feeds.htm`。从当前运行机器复用产品HTTP请求实抓：政策源HTTP200/15条，CNBC头条与金融市场源均HTTP200/30条。两个CNBC源均包含11月18日沃勒采访的同一原站地址，规则识别为官员表态线索、说话人Christopher Waller；官方政策源包含11月18日的FOMC会议纪要，标为官方原文/纪要线索。

CNBC取样中的Feed时间为`2026-11-18T15:13:39+00:00`，不能据此推翻先前中文快讯或采访时间；它只是Feed标注值，可能是更新时刻。本轮因此明确`feed-reported-time`，不声称已经取得原文章最早发布时间或完整采访。CNBC仍按标题元数据权利范围处理，未抓付费全文。

以上是Git外真实来源取样，不是正式新闻库或生产投递记录。JSON位于`/tmp/instant-ai-policy-live-20261119/source-verification.json`。

## 实现定位

- `product/instant_ai/monetary_policy.py`：识别、重点锁定、来源/时间分层、相同URL/标题合并、短期待办、重试和匹配回执。
- `database.py`：schema16及第31条政策原始来源；`service.py`：同事务接入、重点源优先和重分类保护；`server.py/watch_events.py`：独立循环和已有新闻轮次交付、认证后跟踪投影。
- `client/instant-ai/src/instant-ai/WatchEventsPanel.ts`：原链接、来源时间和发现时间、媒体与官方分层、真实交接阶段。
- 罗盘`mcp/monetary-policy-intake.mjs`、`state-store.mjs`、`local-bridge.mjs`：新信号类型、短期能力、严格公开字段和原表回执；`event-intelligence-worker.mjs/chatgpt-event-service.mjs`：原GPT队列、来源核验提示和同主题历史；`public-event-evidence.mjs/managed-event-ai.mjs`：公共证据边界，不新增模型任务。

## 自动与界面检查

- 完整Python355项通过，无跳过；新增利率8项、隔离真实HTTP1项和调度故障隔离1项，既有日历跨系统测试也在真实罗盘源码下通过。日志`/tmp/instant-ai-policy-python-20261119.log`。
- 真实HTTP：单次投递收到匹配hash和ID、重读零新增、媒体出处保持、转发请求403、代理响应不含能力，合成投资状态逐项不变。不是模拟sender成功，也没有写正式数据库。
- 罗盘`./deploy/cloud-dev/check.sh`返回`CHECK_OK`并生成预览；政策单测和扩展HTTP检查包括旧官方信号回归。后续补充Feed时间标记、媒体输入及关联分组后，受影响测试再次通过。日志`/tmp/compass-monetary-policy-check-20261119.log`。
- 前端0.34.0构建和受跟踪静态资源逐字节同步通过；既有16项前端契约通过，390/1280px政策卡28项和日历26项通过。采用纽约浏览器时区仍显示正确北京时间；未知时间不填当天、无横向溢出、外部文字不执行、跳转保留noopener。
- 截图、临时模块与编译输出在`/tmp/instant-ai-policy-ui-20261119/`，没有提交原始数据、日志、缓存或构建中间产物；产品既有受跟踪静态发行文件按项目原流程更新。

## 11月19日开发验收时的边界和待办

11月19日开发验收时，正式版本仍为即时AI0.33.0，配套接收代码也未发布。真实采访内容的GPT核验、实际完整报告回填和手机通知须在正式发布后用自然消息验收；本轮未调用真实模型或发送手机通知。五家央行是现有渠道的识别范围，不表示新增所有地区联储/所有媒体全量覆盖。

Windows PowerShell记忆检查无法在当前Linux直接运行；旧脚本还写死历史`GRANDPAAMU_0_21_1_LIVE`，本轮不伪造旧检查点。必需文档、永久单主人规则、当前源版本及Git外运行数据边界另做Linux等价核对，保留该旧脚本限制。


## 2026-11-20正式发布追加

所有者明确“发布”，即时AI预检READY后执行`sudo -n /usr/local/sbin/instant-ai-publish`，生产功能提交`75c1db917ec8be287c1b6e486aeba1f82a1247d9`/0.34.0。服务器运行355项，353通过、2项可选跨仓因发布环境未配私有源码而跳过；两条跨系统HTTP在此前开发完整355项中实际通过。公网/回环0.34.0、app.js/styles.css/sw.js一致，5项私有API401、诊断公网/代理404、主人30天会话通过。

随后执行`sudo -n /usr/local/sbin/time-compass-publish`，完整检查与预览CHECK_OK，正式release为`20261120-191440`、功能提交`f19183d6e12243a51fee30e24ee70e85af7909b9`。原始时间线预检及导入unchanged=true、home/zijin新增更新均0；回环bridge/MCP及正式MCP元数据200，公开首页转登录200、匿名公网API401，7配套模块字节一致。标准发布器按原流程重启罗盘/MCP/隧道/Caddy；没有额外域名或北京操作。

正式回环contractVersion=1、15分钟政策能力与完整回执目录已启用；代理不露能力、转发POST403。19:14:25与19:19:25独立日历自然两轮connected，6条时点明确/1条日期待定，待投递失败0。再次实抓Fed政策15条、CNBC两源各30条，均200；三源首次接入24小时窗口内可识别政策条目0。正式政策回执和policy_update待办均0，因此首次自然信号的匹配hash/ID、原GPT取件及完整报告仍未通过正式验收。没有向生产注入合成事件或重放过期采访；日历自然心跳不充当政策交接证据。

原GPT配置保持chatgpt-task，状态waiting_host_task，活动Events订阅0，宿主任务本身/实际模型不可由MCP证明；不能据此认定主动读取任务不存在，没有启用云端CLI代写。推送配置可用但手机实际送达未验收。P3-POLICY-01保留IN_PROGRESS，下一步为首条自然消息正式回执及原GPT待办核对。

Git外证据：`/tmp/instant-ai-publish-0340-20261120.log`、`/tmp/compass-policy-publish-20261120.log`、`/tmp/instant-ai-0340-release-checks-20261120.json`、`/tmp/instant-ai-0340-natural-next-tick-20261120.json`、`/tmp/instant-ai-policy-release-source-probe-20261120.json`、`/tmp/compass-policy-release-health-20261120.json`、`/tmp/compass-policy-release-pipeline-20261120.json`。JSON只保存安全投影，不含令牌或主人投资内容。


发布机器为新加坡云端。即时AI开发工作区`/home/compassdev/Documents/Codex/2026-08-26/instant-ai-finance`在发布前后功能提交均`75c1db9`，与origin/main一致；罗盘工作区`/srv/time-compass-dev/workspace`按统一协议同步前后均`f19183d`，无分叉或其他现场修改。生产分别由`edff596`和`368ef03`升级至上述功能提交；本次收尾只追加发布记录，不再次重启应用，文档提交号与生产功能提交号分别保留。
