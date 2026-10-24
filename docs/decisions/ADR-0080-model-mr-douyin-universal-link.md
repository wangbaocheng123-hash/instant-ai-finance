# ADR-0080：模型先生原作品在 iPhone 以抖音 Universal Link 直达

- 状态：`ACCEPTED / IMPLEMENTED / PUBLISHED`
- 日期：2026-10-22
- 决策者：产品所有者

## 背景

0.28.1 恢复了“打开抖音原作品”入口，但手机真机验收表明，点击后仍可能不能直达对应作品。旧前端的首跳是`https://www.douyin.com/video/<作品ID>`，并且使用`target="_blank"`。

2026-10-22 核对的公开关联文件显示：正式抖音 App 在`www.douyin.com`上没有关联`/video*`；`m.douyin.com`则关联`/share/video*`和`/share/note*`。iPhone 请求`www`作品页可能再 302 到`m` 分享页，但 Apple 的 Universal Links 契约不应依赖“先进浏览器，再重定向到关联路径”完成 App 交接。

## 决定

1. 后端继续保留已验证的抖音来源 URL，不把导航适配重写为原始证据。
2. iPhone/iPad 渲染精确作品路径时，把首跳确定性改为：
   - `video/<15–22位ID>`或`share/video/<ID>` → `https://m.douyin.com/share/video/<ID>`；
   - `note/<ID>`、`article/<ID>`或`share/note/<ID>` → `https://m.douyin.com/share/note/<ID>`。
3. 由作品 ID 确定性构造的导航 URL 不带查询串或片段，避免把网页追踪参数带入 App 交接。
4. iPhone/iPad 保留真实`<a href>`和用户点击，不新建浏览上下文；电脑端仍保留原已验证 URL 并新标签打开。
5. 真实`v.douyin.com/<短码>`无法从作品 ID 反推，继续保留，不伪造短码。不硬编码非公开稳定契约的`snssdk1128://`私有 scheme。
6. 仍只接受 HTTPS 的`douyin.com`及其子域，拒绝账号口令式 URL、异常端口和伪造域名。未来若使用抖音开放平台生成的时效 schema，必须单独决定应用凭据、权限和过期处理，不属于本次修复。

## 备选项与理由

- 仅去掉`_blank`：不解决首跳域名/路径未与正式 App 关联的根因。
- 继续依赖`www`到`m`的 302：首跳仍是普通网页 URL，无法作为直接 App 交接契约。
- 由 ID 伪造`v.douyin.com`短码：短码不可推导，会破坏“精确原作品”要求。
- 立即接入开放平台 schema API：需要新的应用凭据、`jump.basic`权限和过期链接生命周期，对本次已有精确作品 ID 的修复过重。

## 验收与证据

- TypeScript/Vite 0.28.3 生产构建通过，构建产物与 Python 静态目录一致。审计新发布公告后把Vite开发依赖从6.4.3更新到6.4.5，`npm audit`为0漏洞。
- 模型先生浏览器回归增至26项：显式 iPhone UA 下验证 video/note/article 首跳和顶层点击的精确 URL，同时覆盖真短链保留、桌面行为、无效链接拒绝、学习标记与播放器不回归。
- 完整277项Python、38项播放器、2项模型先生面板和10+6项前端契约回归通过，没有访问生产资料或发起付费调用。
- Chromium 可验证真实点击发起的网络目标，不能代替 iOS 对本机已安装抖音的系统级交接；正式发布后仍须所有者用 iPhone 真机点击验收。
- 以iPhone Safari UA对已知最新精确作品`7695675202135444145`请求`m.douyin.com/share/video/<ID>`得到HTTP成功响应，页面保留同一目标ID；同时实时解析AASA确认正式关联路径含`/share/video*`和`/share/note*`。
- 公开契约证据：[Apple Universal Links 调试说明](https://developer.apple.com/documentation/technotes/tn3155-debugging-universal-links)、[`www.douyin.com` AASA](https://www.douyin.com/.well-known/apple-app-site-association)、[`m.douyin.com` AASA](https://m.douyin.com/.well-known/apple-app-site-association)、[抖音开放平台视频详情跳转接口](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/jump/item-info)。

## 发布边界

2026-10-24，所有者明确要求“发布”。固定受限发布器将生产仓库从`3668b6c`安全快进到`211581d4726d8e41bdade89dbb4a1b7b42c0dbae`，服务器277项Python回归通过。回环与公网健康均为0.28.3，Service Worker为`instant-ai-shell-v0.28.3`，公网静态摘要与生产仓库一致，`instant-ai.service`与Caddy均为active，未登录私有入口继续401。

发布已完成，但服务器和Chromium不能代替iOS系统级Universal Link交接；最终仍需所有者用安装了抖音的iPhone点击同一作品验收。
