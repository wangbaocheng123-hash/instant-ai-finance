# ADR-0060：THE ELEC 半导体原始渠道与单主人 Web Push

- 日期：2026-10-10
- 状态：`ACCEPTED / IMPLEMENTED / NOT RELEASED`
- 候选版本：即时 AI `0.27.0`
- 决策者：产品所有者
- 关系：扩展 ADR-0040 的韩国分层来源和 P2-NOTIFY-01；不改变短周期留存、单主人、无自动交易及模型先生 ChatGPT 事件通知。

## 背景与边界

阿斯麦韩国维护零部件统一涨价 10% 的消息最早由 THE ELEC 原站发布，但现有 26 条采集线路只有韩国综合媒体索引、券商晨会和 Stockplus 快讯，没有 THE ELEC 官方 RSS，无法稳定证明系统是否在第一渠道发现该消息。所有者要求把该渠道纳入采集，并让即时 AI 自身把相关重要消息通知到手机。

本决定只增加公开文字元数据采集和主人设备通知。它不抓取付费正文、不自动交易、不增加第二套用户/权限系统、不把通知或观点写入罗盘，也不替代 ChatGPT 中模型先生连续解读的独立通知链路。候选功能未获本轮正式发布授权；生产仍为 0.26.0。

## 决定

1. 新增 `the-elec-semiconductor`，直接读取 THE ELEC 官方半导体 RSS `https://www.thelec.kr/rss/S1N2.xml`，不再依赖 Google/Bing 转发。该源是“专业媒体原始报道”，可信度 4/5，明确不是阿斯麦公司公告。
2. 只把标题、原站链接、发布时间和发布方投影进情报条目；`title_link_only` 和 `rights_scope=title_date_link_only` 禁止把 Feed 摘要冒充正文。公开 Feed 原始响应仍按现有短周期证据规则保存和清理。
3. THE ELEC 的无偏移发布时间按韩国 `UTC+09:00` 解释并统一存为 UTC，避免把 `2026-10-10 09:37:29` 错当 UTC。阿斯麦标题规则补充 `ASML/阿斯麦` 实体以及韩文涨价词 `인상/부품값`，本例归为“价格/宏观”。
4. 专业源提醒要求同时满足：可信度至少 4、重要度至少 75、有已跟踪实体、事件属于中断/政策/业绩/并购/产量/价格之一。默认其他来源继续使用原有 85 分门槛。原因中明确“专业媒体原始报道（非公司公告）”。同一条目每个通知通道只有一条 outbox，后续重复采集或补充证据不重复提醒。
5. 即时 AI PWA 使用标准 Push API、Notifications API 和 Service Worker，把通知送至主人已授权的设备。iPhone/iPad 需要 iOS/iPadOS 16.4+、先从 Safari“添加到主屏幕”，再由主人点击页面“手机通知”按钮触发授权；不把普通网页前台提示冒充后台手机通知。
6. PWA 清单切换为 `display=standalone` 并固定 `id=/`；Service Worker 只展示最小公开消息标题，点击后回到主人登录保护的 `/?item=<id>` 详情。锁屏完整显示正文不作为成功条件。
7. schema 12 新增 `web_push_subscriptions` 和 `web_push_deliveries`。Push endpoint、浏览器公钥和 auth secret 只保存在既有 Git 外 SQLite 运行库；长期 VAPID P-256 私钥只保存在 `即时AI文件库/notifications/` 或云端 `/var/lib/instant-ai/notifications/`，创建权限 0600。无用户表、外部记忆服务、报告或观点记录。
8. 服务端按 RFC 8291 `aes128gcm` 加密通知、按 RFC 8292 ES256 VAPID 签名。推送目标只接受 Apple Web Push、FCM、Mozilla Push 和 Windows Notification Service 的 HTTPS 443 主机，拒绝任意 URL、私网和非常用端口，避免订阅接口成为 SSRF 出口。
9. 主人开启通知时立即发送一条真实测试通知。只有设备订阅已经存在之后产生的合格新消息才进入 Web Push outbox，不补发历史库存；失败按 5 分钟采集节奏有界重试五次，404/410 立即停用过期订阅。关闭通知只停用该设备，不删除财经资料或站内提醒。
10. 所有 POST 继续要求主人会话和 `X-Instant-AI: 1`。状态接口不返回 endpoint、浏览器密钥、VAPID 私钥或投递正文；生产真实手机送达必须在正式发布后由主人点击开启并单独验收。

## 验证与发布门禁

- THE ELEC 官方 RSS 隔离实测 HTTP 200、20 条，目标 ASML 标题唯一命中；发布时间规范化为 `2026-10-10T00:37:29+00:00`。
- 单测覆盖韩文分类、韩国时区、来源权利边界、schema 迁移、订阅目标白名单、0600 稳定 VAPID 密钥、RFC 8291 设备端解密、首次测试通知、专业源提醒、重复调度不重发。
- 完整 250 项 Python 回归、16 项前端契约、TypeScript/Vite 0.27.0 构建和 npm 0 漏洞审计通过；测试只用合成订阅和本机解密，没有向真实手机或第三方 Push endpoint 发送测试请求。
- 正式发布仍须所有者再次明确确认；发布后先核对 27 条来源、服务健康和公网 0.27.0，再由主人按页面指引开启通知，分别验证测试通知和下一条自然重要消息。代码上线、订阅保存、Push 服务 201/202 与手机锁屏实际出现分别记录，不互相冒充。

## 回滚

回退应用代码即可停止新来源采集和新通知生成；已入库公开条目按原短周期规则自然清理。保留 Git 外订阅账本和 VAPID 私钥，避免回滚/重发造成历史通知；如所有者明确要求撤销某设备，可从页面关闭。不得为回滚修改时变罗盘、北京采集器或模型先生事件订阅。

## 依据与实现位置

- THE ELEC 官方 RSS 索引：`https://www.thelec.kr/rssIndex.html`
- Apple Web Push for Web Apps：`https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/`
- `product/instant_ai/database.py`：第 27 条来源、schema 12 与 Git 外订阅/投递账本。
- `product/instant_ai/collectors.py`、`rules.py`：韩国时区和 ASML/涨价确定性分类。
- `product/instant_ai/web_push.py`、`service.py`、`server.py`：订阅校验、加密签名、outbox 和主人 API。
- `client/instant-ai/public/manifest.webmanifest`、`sw.js`、`InstantFinanceApp.ts`：可安装 PWA、通知授权和点击回到详情。
- `product/tests/test_web_push.py`、`test_core.py`：合成端到端回归；不等同真实手机验收。
