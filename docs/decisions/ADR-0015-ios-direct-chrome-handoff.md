# ADR-0015：iPhone 原文按钮直接交给 Chrome

- 状态：`ACCEPTED`
- 日期：2026-08-28
- 决策者：用户在手机实测 0.9.1 后，明确要求“浏览器翻译原文”直接跳转到谷歌浏览器
- 补充并局部替代：ADR-0014 的 iPhone 原文打开方式

## 背景

0.9.1 使用普通 HTTPS 新标签页打开原文。在 iPhone 主屏幕入口的实际流程中，这会先进入一个没有 Chrome 翻译菜单的中间页面，用户必须再次选择“通过谷歌浏览器打开”，进入 Chrome 后才能使用网页翻译。该两步流程与按钮名称和用户预期不一致。

完整正文仍属于发布方网页。即时 AI 不应为了消除一次跳转而重新抓取、保存或翻译正文，也不应接管 Google 账户或绕过来源限制。

## 决定

1. 0.9.2 在 iPhone/iPad 上把经过 HTTP/HTTPS 白名单校验的原文地址转换为 Chrome iOS 官方 URL scheme：`https://` 对应 `googlechromes://`，`http://` 对应 `googlechrome://`。
2. “浏览器翻译原文”保持为用户主动点击的主按钮；点击后直接把当前原文交给已安装的 Chrome，不再先创建普通中间网页标签。
3. iPhone 详情页同时保留“普通浏览器备用打开”HTTPS 链接。Chrome 未安装、系统拒绝 scheme 或来源地址异常时，用户仍有可见的恢复路径。
4. Windows、Android 和其他非 iOS 环境继续使用普通 HTTPS 新标签页，不强行指定浏览器。
5. Chrome 负责页面翻译。即时 AI 不能自动替用户点击 Chrome 的“翻译”或“始终翻译英语”，也不承诺消除 iOS 首次跨应用确认提示。
6. Service Worker 外壳缓存升至 0.9.2；采集、短期保存、摘要备用、API `no-store` 和五分钟自动更新机制不变。

## 理由

Chrome 的 iOS scheme 能从主屏幕 Web 入口直接切换到 Chrome，同时不引入正文抓取、服务器代理、额外存储或翻译费用。显式 HTTPS 备用链接避免把产品锁死在单一浏览器安装状态。

## 限制

- 直接跳转要求 iPhone 已安装 Chrome；系统可能在第一次调用时显示一次跨应用确认。
- 原站登录、付费墙、地区限制、脚本兼容性和 Chrome 翻译质量仍由发布方、Chrome 与当前网络环境决定。
- URL scheme 只用于用户点击后的外部导航，不用于后台请求、采集或跟踪。

## 验收证据

- TypeScript/Vite 0.9.2 生产构建成功；生成客户端同时包含 `googlechromes://`、`googlechrome://` 和普通 HTTPS 备用入口。
- 18 项 Python 单元测试通过；移动外壳测试固定直接 Chrome scheme、备用入口与 0.9.2 Service Worker 缓存契约。
- `npm audit --audit-level=high` 为 0；本机完整运行验收通过，`/api/health` 返回 0.9.2。

## 资料

- Chromium 官方 iOS 文档：[Opening links in Chrome](https://chromium.googlesource.com/chromium/src.git/+/master/docs/ios/opening_links.md)

## 关系

本 ADR 只替代 ADR-0014 中“iPhone 只能交给默认浏览器”的实现限制；ADR-0014 的不抓取正文、摘要备用和浏览器负责翻译等决定继续有效。
