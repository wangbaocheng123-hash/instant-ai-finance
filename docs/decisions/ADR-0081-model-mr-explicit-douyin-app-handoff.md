# ADR-0081：模型先生显式抖音客户端入口与可操作的后备

- 状态：`ACCEPTED / IMPLEMENTED / PUBLISHED / AWAITING IPHONE ACCEPTANCE`
- 日期：2026-11-06
- 决策依据：所有者反馈0.28.3发布后点击源链接仍无反应，继续完成已授权的同一跳转修复。
- 部分替代：ADR-0080的“仅以HTTPS分享页作为iOS主入口”及article到note转换。

## 现场证据与限制

线上健康、Service Worker与脚本摘要确认仍为0.28.3/`211581d`，与仓库相符。原按钮为真实锚点，页面委托、CSS、CSP、Service Worker均未发现吞掉外链点击的代码。所有者反馈说明0.28.3的手机端验收未通过，不能继续称跳转已解决。

`m.douyin.com`的公开AASA仍包含`/share/video*`、`/share/note*`；这只是App关联配置，不能证明某台手机交接成功。本轮请求也观察到分享页有时返回Argus验证页，HTTP 200不足以证明对应作品可访问。当前设备的浏览器、域名打开偏好与系统行为未能由服务器确定，故不声称已定位到单一手机端根因。

更正ADR-0080对302的绝对判断：[Apple TN3155](https://developer.apple.com/documentation/technotes/tn3155-debugging-universal-links)明确允许另一App打开的链接经重定向进入Universal Link，只是不推荐；第三方浏览器是否实现Universal Link由浏览器厂商决定，用户对域名的打开偏好亦会影响后续行为。没有证据证明所有iOS主屏Web App都禁止唤起。

## 官方当前源码证据

本轮实时读取的抖音分享页引用官方脚本[1123.72bf221b.js](https://lf-douyin-mobile.bytecdn.com/obj/growth-douyin-share/growth/douyin_ug/static/js/1123.72bf221b.js)，SHA-256为`88c3458387d932fa00f59d98372126365e6a89914bad09a6b528619437ca965d`。该脚本的`detail`路由、`B.d.ios`、`v.d`、`_genSchema`共同构造外部iOS视频地址：`snssdk1128://aweme/detail/<作品ID>?refer=web&needlaunchlog=1`；`aweme://`仅是其`inApp`分支。

普通note的[官方页面脚本](https://lf-douyin-mobile.bytecdn.com/obj/growth-douyin-share/growth/douyin_ug/static/js/async/note_(id)/page.704435eb.js)同样向SDK传`type:detail`与`item_id`。长文article未核实相同客户端路由，保留原HTTPS地址。这是当前官方实现证据，不将其当成永久不变的开放平台承诺，也不复制第三方源码。

## 决定

1. iPhone/iPad的精确video/note来源，主按钮使用上述客户端URI；ID只从已校验的HTTPS抖音来源路径提取，保持15—22位字符串，不使用内部库编号，不接收外部自定义scheme。
2. 主入口为真实锚点，在原始点击中同步发出。提示只更新局部节点，不阻止默认导航，不等待网络、异步解析、iframe或定时重试。
3. 同时显示精确作品HTTPS“原网页”入口，使用新浏览上下文，为主屏Web App提供外部浏览器出口；这仍不保证系统唤起。
4. 提供“复制链接”。浏览器剪贴板拒绝或缺失时显示只读输入与长按复制提示。用户操作只在当前页面，不保存剪贴板内容或建立新存储。
5. 提示只说明“请允许打开”“若没有打开可使用后备”，不自动断言App已安装或跳转成功，不自动下载App、反复唤起或延时跳走。
6. article及真实短链继续HTTPS，不猜短码；桌面保留来源网页新标签契约。复制、提示和跳转不重建正在播放的视频或祖先节点。

## 验收与发布

0.28.4使用新Service Worker缓存。32项链接/学习浏览器和6项追加验证通过，覆盖真实点击发出的URI、详情播放中操作、原网页新页导航、复制成功/拒绝/API缺失、standalone/CriOS/iPad桌面UA。38项播放器、277项Python、16项前端契约与生产构建亦通过。浏览器通过捕获原生点击来代替OS处理，不冒充iPhone抖音客户端验收。

沿用所有者对同一故障的修复与发布授权，完成测试后走即时AI既有固定发布器。最终仍需所有者手机确认“点击→允许打开→对应作品”结果。

2026-11-06已经固定发布器上线精确提交`976fcf5c9b19b6bcdb3d3877310022ef077f007f`。服务器277项通过，回环/公网健康与SW为0.28.4，公网JS/CSS摘要一致，服务active及未登录401门禁正常；手机系统级交接仍待所有者验收。
