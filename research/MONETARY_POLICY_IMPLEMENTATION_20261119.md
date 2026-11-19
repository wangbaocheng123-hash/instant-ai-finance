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

## 已知边界和待办

当前正式版本仍为即时AI0.33.0，配套接收代码也未发布。真实采访内容的GPT核验、实际完整报告回填和手机通知须在正式发布后用自然消息验收；本轮未调用真实模型或发送手机通知。五家央行是现有渠道的识别范围，不表示新增所有地区联储/所有媒体全量覆盖。

Windows PowerShell记忆检查无法在当前Linux直接运行；旧脚本还写死历史`GRANDPAAMU_0_21_1_LIVE`，本轮不伪造旧检查点。必需文档、永久单主人规则、当前源版本及Git外运行数据边界另做Linux等价核对，保留该旧脚本限制。
