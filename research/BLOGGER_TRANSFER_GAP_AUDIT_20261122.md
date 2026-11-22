# 北京大杂烩已下载但未发送：2026-11-22只读排查

## 结论

最新作品没有发到新加坡，故障位于北京“本地采集完成 → 生成传输清单/入outbox”边界。`CollectorContentReadyAdapter.export`在检查大杂烩的来源身份时，因`platform_user_id`为空拒绝导出；不能把它归因于新加坡拒收、网页缓存或两条旧网络重试。

本轮只读核对生产HTTP、SQLite的mode=ro查询、指定作品状态及脱敏日志，没有修改配置、数据库、媒体、采集任务、服务或正式分支，也没有触发下载、转写、AI或补发。新增的仅为仓库审计记录。

## 真实作品与阶段证据

| 北京记录 | 抖音作品ID | 作品 | 采集/失败通知时间（北京时间） | 文件 |
|---|---|---|---|---|
| 66 | 7710190654587948326 | 紫金矿业大跌，到底是情绪下杀还是逻辑崩盘？ | 2026-11-22 10:20:42 | 7,915,095字节MP4，stored，实际文件存在、大小及SHA-256匹配 |
| 65 | 7704964425870730099 | 当前采集标题为01:16，不猜原题 | 2026-11-06 19:56:36 | 3,657,692字节MP4，stored，实际文件存在、大小及SHA-256匹配 |

66的作品发布时间是2026-11-20 17:57:32，不能和今天的采集时间混为一谈。两条均归属“大杂烩”，评论关闭、现有评论0；文件存在而没有生成传输记录，故无需为恢复交接再次采集视频。

- 北京`/health/version`返回1.2.0、`10fdb3a313f8851c8e7963fb29bc2935dfb164ae`；采集器、模型web及下载worker均active。即时AI公网健康200/0.34.0，主人保护开启。
- 北京`state.json`中两条均`content_ready_pending=true`，错误逐字为：`博主缺少已持久化的 platform_user_id。`。最新记录的通知失败时间为`2026-11-22T02:20:42.023498+00:00`。
- 同时段服务日志明确写出“已保存”与“已完成本地采集，但后续处理回调失败”；未把日志中的“待重试”当成已经自动补发。
- 对北京outbox的只读查询没有这两个source_work_id。它们尚未取得传输ID，更没有新加坡完成回执。
- 新加坡博主只读MCP的全部作品查询返回57条，按发布时间列出的最近作品仍为10月23日；没有本次11月作品。该投影只读，没有触发采集或AI，也不能与北京66条总数简单相减推断丢失数量。
- 传输配置ready、近期仍有自然delivered记录。当前2条retry_wait实际为模型先生7683810974976986289和7681209106778645105的旧媒体network_error，与65/66不属于同一故障；保留独立历史任务，不顺带重试。

## 源码定位与补办边界

对照生产提交10fdb3a与本轮开发基线ccdcfe1：

1. `mx_agent/collector_export.py::CollectorContentReadyAdapter.export`在reserve/enqueue之前校验creator_uuid与platform_user_id；因此缺来源身份不会形成outbox待办。
2. `mx_agent/creator_sync.py::_notify_content_ready`捕获异常并记录pending/error，保留已下载数据。主程序未见独立读取content_ready_pending并补办的消费者，不能保证不再次运行采集时会自行恢复。
3. `mx_agent/collector_server.py::creator_summaries`只用sync.last_error判断has_error，没有纳入last_content_ready_error，因此本次dashboard实际has_error=false、显示采集已完成，掩盖了交接失败。
4. 同文件`retry_transfers`仅调用`TransferOutbox.expedite_retries`，后者只提前现有retry_wait记录的下次时间；点击此按钮不能修复这两个尚未入队的作品。
5. main中的1.3.0候选`creators.py::_misc_creator/_normalize`已经为大杂烩设置固定`instant-ai-miscellaneous`来源身份，转为link_only并保留原creator_uuid；它尚未在北京上线。运行key会从旧creator-b723cf6204a6变为miscellaneous，旧待办状态不能被假定自动迁移。

具体修复应合并为一次可验收任务：补齐固定身份并保留UUID；按65/66及真实作品ID复用现有文件、使用既有导出/outbox/签名回执精确补发；补齐发送前失败的可见提示与受控补办入口，只有匹配完成回执后才显示送达。补发旧条必须显式处理，不能仅升级版本后声称已经恢复。后续从即时AI博主页核对相同“大杂烩”UUID下两条作品、媒体可读和处理状态；不新建另一资料库、不反向轮询北京或重抓评论。

本次请求为排查，没有执行正式发布器、推进beijing-production或手工修改生产字段。后续修复发布继续使用原统一套件发布边界，不影响模型下载器、社区、域名或罗盘。

## 本轮验证限制

只读生产证据已足以核定上述根因。尝试复跑3项既有聚焦检查（旧大杂烩UUID迁移、固定身份导出、回调失败保留）时，系统Python与现存Git外venv均在导入阶段缺少Pillow/PIL，测试未执行成功；没有安装依赖或把历史237项通过冒充本轮复测。日志仅在`/tmp/blogger-transfer-audit-20261122/`，没有生产资料、原文、日志或凭据进入Git。没有新的产品代码变更，本轮文档另做差异、历史字节及敏感文件范围检查。
