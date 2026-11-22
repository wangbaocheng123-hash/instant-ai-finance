# 北京博主发送交接修复与开发验收

2026-11-22，开发基线f4d2c7f。根因与初次证据见BLOGGER_TRANSFER_GAP_AUDIT_20261122.md；本文件记录后续实现，不将开发成功写成生产补发成功。

## 已完成

- 大杂烩固定来源身份复用1.3.0候选，额外保留原runtime key与UUID。旧pending状态原地加载，规范别名与旧ID均返回同一库，不丢旧状态、不改名或迁移现有媒体。
- 内容回调前保存持久意图；入队后保存transfer_id。既有sender循环按退避补办，已有浏览器任务优先；任何单条恢复错误均不阻塞现有上传。主人按钮可补办未入队项，原网络队列继续使用旧退避/回执。
- 相同公开快照复用outbox记录，崩溃/重启或重复补办不新增修订；真正改变标题/评论/媒体证据仍生成下一修订。身份不匹配、缺媒体及死信保留核对，不自动重下或重新识别。
- 摘要真实暴露发送前失败，页面新增待打包和历史待核对统计，按钮为“补发/重试发送”；PWA壳v7。下载结束提示与回执送达分开，错误详情/路径不进入页面。

## 新补充的只读现场

进一步核对旧runtime状态，大杂烩实际有5条同因待办：本地42/7695318767240252708、43/7699719856337950820、64/7694227852975745445、65/7704964425870730099、66/7710190654587948326。五条作者均为大杂烩、媒体文件存在，65/66已额外核对大小和SHA-256。发布后应全部按原身份与现有文件恢复并匹配回执，不能只处理最近两条。

另有5条普通博主的旧pending曾记控制字符错误，可能已有后续送达修订。此类无重试版本标记的历史项单列待核对，不自动重放；本次不认定它们都是缺失作品。2条旧模型先生媒体网络重试仍独立处理。

现场应用仍1.2.0/10fdb3a，采集器/模型worker/模型web/社区/Caddy均active；本轮未重启服务、执行正式发布器或推进beijing-production，没有真实下载、转写、模型调用、补发或生产数据库写入。

## 本轮检查

- 恢复原有Git外venv所需requirements，仅在/tmp/codex-beijing-collector-venv安装项目依赖，未装全局包或下载Chromium。此前PIL导入阻断已经消除。
- 完整250项Python通过，无跳过。真实隔离Storage/outbox覆盖原runtime恢复、相同文件、重启不重复、证据变化新修订、退避和force、错作者/作品、媒体缺失、忙锁让位、旧资料不重放、死信、入队后状态写失败；API/后台摘要及发送器隔离同时验证。
- app.js和service-worker.js的node语法检查、完整包内手机宽度/PWA/部署契约及git diff --check通过。不是手机真机、正式回执或新加坡新作品验收。
- 日志、隔离数据库与依赖安装记录仅在/tmp/blogger-transfer-audit-20261122/，本轮最终日志为acceptance-suite.log。无生产库、媒体、原文、Cookie或密钥进入Git。

## 正式发布后唯一验收链

收到明确北京采集器发布确认后，先安全推进已测试main提交到beijing-production，再走固定套件发布器。只应更新采集器子树，模型下载器源码没有本次差异，不修改共享网关、域名、社区或罗盘。

确认公网精确1.3.0/SHA、timer仍disabled/inactive；核对5条沿原UUID入队，复用原媒体并取得匹配完成回执；由新加坡只读MCP检查同一大杂烩、作品/原链接/媒体与既有处理状态。新加坡接收后仍服从原处理开关与收费审批，不能把补发当作新增AI授权。若仍有失败，保留原文件/回执证据，不能改数据库状态假装送达。


## 2026-11-22正式发布与真实补发验收

所有者明确“发布”并要求继续后，新加坡云端工作区`/home/compassdev/Documents/Codex/2026-08-26/instant-ai-finance`在干净main上执行北京通道预检READY；main/origin/main均为`73a7aaf3ff822835894093f34a080f53e7c6b20d`。按既有脚本安全快进beijing-production，从`10fdb3a313f8851c8e7963fb29bc2935dfb164ae`推进到该功能提交；仅调用一次北京固定`sudo -n /usr/local/sbin/beijing-suite-publish`，未直接编辑生产代码或写数据库状态。

北京隔离构建完整250项通过，collector发布service Result=success、ExecMainStatus=0；套件返回BEIJING_COLLECTION_SUITE_PUBLISH_VERIFIED，公网等待器返回BEIJING_GIT_PUBLISH_OK。公网/回环版本为1.3.0与完整73a7aaf功能SHA，deployed_time为2026-11-22T05:23:24Z（北京时间13:23:24）。模型下载器发布器报告子树未变，未重建或重启模型组件。

大杂烩沿旧runtime key `creator-b723cf6204a6`及原creator_uuid恢复，固定来源身份补齐，配置中仍只有一个同名库。5个原文件的字节数及SHA-256与发布前逐项一致；10个媒体/评论artifact均verified、uploaded_bytes等于size_bytes。新发送记录全部revision=1、attempt_count=0、status=delivered，receipt_id匹配transfer_id；原pending标记全清、错误为空。没有为恢复执行新抖音采集、重下载、重抓评论或重新识别。

| 北京本地记录 | 真实作品ID | 完成回执前缀 | 新加坡只读核对 |
|---|---|---|---|
| 42 | 7695318767240252708 | d66a86e3e568d104 | 同一大杂烩，awaiting_asr_approval |
| 43 | 7699719856337950820 | 7dbe93b8d370cdbe | 同一大杂烩，awaiting_asr_approval |
| 64 | 7694227852975745445 | c1e4e5915366f8ad | 同一大杂烩，awaiting_asr_approval |
| 65 | 7704964425870730099 | fefb565ac4b413c8 | 同一大杂烩，awaiting_asr_approval |
| 66 | 7710190654587948326 | 6974b8680c7346b7 | 同一大杂烩，awaiting_asr_approval |

新加坡MCP按“大杂烩”只读查询准确返回5条，全部作品ID与北京回执对应，最新“紫金矿业大跌……”及11月6日作品均可见，真实原抖音链接保留。当前原文处理状态为awaiting_asr_approval，不能把视频接收完成当作原文识别或解读已经完成；没有增加收费授权或调用AI。

13:24:39及13:27:11自然发送循环的两次投影中，5条transfer_id/修订/送达状态完全相同，未产生重复记录，大杂烩待入队数0。既有普通博主5条legacy_review及模型2条网络重试保留；未将它们假报为此次恢复完成。

生产5个关键Python模块及3个前端文件共8项SHA-256与发布候选完全一致，回环实际返回的app.js也一致。公网版本200、collector匿名页面302登录、即时AI0.34.0健康200、私有博主API401通过。发布前后InvocationID仅blogger-collector.service变化；模型worker、模型web、社区和北京Caddy均active且InvocationID保持。Git发布timer仍disabled/inactive，未启用任何Git轮询或调用新加坡/罗盘发布器。

Git外证据在/tmp/blogger-transfer-audit-20261122/：publish-branch.log、suite-publish.log、release-baseline.json、post-publish.json、post-observation.json、release-proof.json、source-and-observation-check.json。只保存安全投影，不含凭据、Cookie或签名密钥。发布记录在main收尾提交，生产功能SHA保持73a7aaf，不为文档再次重启服务。

P3-BLOGGER-DELIVERY-02完成。P3-BLOGGER-MISC-01仍保留下一次主人手动指定新链接时的新下载/评论现场验收；本次用现存视频完成真实传输，不为验收重抓一遍。手机真机播放和原文识别审批也不由MCP可见性代替。
