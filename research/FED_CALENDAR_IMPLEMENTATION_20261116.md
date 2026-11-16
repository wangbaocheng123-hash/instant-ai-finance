# 美联储重要会议覆盖修复验收

- 候选版本：0.33.0 / schema15。
- 范围与架构：ADR-0089；原漏检证据另见`FED_CONFERENCE_COVERAGE_AUDIT_20261116.md`。
- 当前生产仍0.32.0；本轮完成开发和测试，没有正式发布。

## 源码与行为

| 部位 | 具体实现及证据 |
|---|---|
| 会议发现 | `product/instant_ai/fed_calendar.py::parse_fed_month/parse_sf_event/sf_upcoming_links`；`future_events.py::_sources/_read_source`增加Board当前/次月及SF索引，复用原官方抓取防护 |
| 本地预告与手机待办 | `calendar_notices.py::queue_calendar_notices`；重要性规则+14天窗口，日期待定/时点确认/改期/临近分开，新闻卡片标发现时间；只调用原通知outbox |
| 持续防重 | `database.py` schema15收据表，新闻过期后保留去重；来源间共用稳定会议身份，重复改期仍可更新；`service.py`投递锁和事件tag防并发重复 |
| 生命周期 | `retention.py::_apply_missing_embedded_dates`和`service.py::reclassify_items`保护日程类型，不把未来会议日期变成新闻发布日期；发生后/失效后7天清理仍在Git外 |
| 采集异常 | `calendar_snapshot`及重点事件页展示真实覆盖/错误/过期/尚未开始，主人能暂停来源；回环诊断仅聚合信息，匿名/代理访问受限 |
| 会后原文 | `database.py::DEFAULT_SOURCES`新增官方speeches和testimony RSS，保留原press RSS，默认来源27→30（其中1个日程控制来源由独立循环管理） |
| 既有接收协议 | 状态回执只发送旧契约允许的字段；存在相互印证的Board来源时使用该真实主证据，不修改接收端白名单，也不为SF独立披露会议伪造来源 |

## 官方实抓

使用正式代码的HTTPS抓取器、独立临时SQLite和空的接收端assignment；没有读取主人凭据或正式新闻库，未向生产节点投递。

- 9个现有及新增官方日历来源完成抓取；Board本月/次月与SF索引状态均正常，原文/来源异常会据实保留。
- 正确识别截图中的会议：Board日程11月16日12:00美东，主办方11月16日09:00 PT，相互印证为北京时间2026-11-17 01:00。
- 同一会议从Board及SF索引发现后合并，只有一个本地预告；同时读取其他近期讲话、FOMC会议纪要、褐皮书及仅公布日期的会议，不把未知时间补成午夜。
- 官方讲话Feed与证词Feed均经产品抓取器HTTP 200、各15条。证词最新条目为7月旧文，沿用7天硬生命周期限制，不因新接来源而把它当作最新消息；原文发布时间不是会议预告发布时间。
- 新Feed以首次接入减24小时为固定历史提醒截止线，旧原文首次导入及再次读取均不发新通知；这不是随时间滚动的24小时限制，后续故障恢复时，首次接入之后真正新发布的讲话仍可补报。

精确时间、来源状态、当前数量和会议证据保存在Git外`/tmp/instant-ai-fed-coverage-20261116/live-verification.json`；数量是本次取样值，不写入永久项目规则。

## 隔离接收与界面验证

使用真实生产接收模块的`assignment/receive/heartbeat/calendar`函数，在新临时目录启动独立回环HTTP接收器，供给端只发送合成会议。首轮home/zijin各收到1个节点，2个回执确认、队列和失败均0，严格心跳通过；重复采集没有新增发现、通知或回执投递。证据在Git外`receiver-contract-verification.json`，生产模块摘要见ADR-0089。本轮没有修改或重启罗盘。

新增`product/tests/test_fed_calendar.py`的27项及1项调度回归；完整345项Python通过（1项旧可选跨仓夹具跳过）、16项前端契约、0.33.0生产构建与10项静态资源逐字节一致通过。浏览器脚本为`client/instant-ai/tests/official-calendar-browser.cjs`，390/1280px共26项检查包括接收端两域同会议去重、待定时间、不冒充发布时间、源故障提示、无横向溢出及外部文字不执行。截图和临时编译模块在Git外。

旧的可选完整罗盘联调需要开发仓库的`test-compass-fixture.mjs`；当前生产安装未携带该测试夹具，因此默认套件保留该项跳过。上面的真实模块隔离回环另行完成，不把它描述为正式接收端或真实投资状态的现场验收。

## 待正式验收

收到发布口令后，按固定发布器上线即时AI，核对schema迁移、30个来源定义、日历独立心跳、公开资源版本与私有接口保护；主人重新进入主屏入口查看预告，并单独验证手机测试通知及自然重要事件通知。保留实际送达结果，不以服务器接受待办或模拟发送器成功替代手机。
