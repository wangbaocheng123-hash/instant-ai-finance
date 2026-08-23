# 安全与风险

> 基线：`RSSNext/Folo@7c220c69a841defbfeeb00a86ed75ad482b22a57`；仅静态审查，未做漏洞利用或运行测试；归档快照无 `.git`。

## 高风险项

| 风险 | 源码证据 | 影响 | 建议 | 状态 |
|---|---|---|---|---|
| Renderer 获得过高权限 | `apps/desktop/layer/main/src/manager/window.ts::createMainWindow` 设置 `sandbox:false`、`nodeIntegration:true`、`contextIsolation:false`、`webviewTag:true` | renderer/XSS 或远端内容链缺陷可能升级到 Node/OS 权限 | 使用 sandbox + context isolation，关闭 nodeIntegration/webview，最小化 preload API | `SOURCE_VERIFIED` |
| 内部 bridge 动态执行脚本 | `packages/internal/shared/src/bridge.ts` 使用 `executeJavaScript` | 与弱隔离窗口配置叠加扩大注入面 | 改为显式、类型化、白名单 IPC | `SOURCE_VERIFIED` |
| Readability 主进程任意 URL 获取 | `packages/readability/src/index.ts::readability`、`ReaderService.readability` | 恶意条目 URL 可探测本机/LAN/云元数据，形成 SSRF | 仅允许 http/https，解析 DNS 后阻断私网/回环/重绑定，限制重定向和大小 | `SOURCE_VERIFIED` |
| 自定义集成任意请求 | main `IntegrationService.customFetch`、desktop integration fetch adapter | 配置/内容若被滥用可访问内网并携带自定义 headers/secrets | 域名 allowlist、凭据隔离、权限确认、审计日志 | `SOURCE_VERIFIED` |
| 凭据明文持久化 | `apps/cli/src/config.ts`、`createSettingAtom`、AI/Integration 设置 | CLI token、BYOK key、集成 token 可能存入 JSON/localStorage | Windows Credential Manager/DPAPI，敏感字段永不进普通设置 | `SOURCE_VERIFIED` |
| 远端热更新信任链 | main updater/hot updater 代码 | 若更新源/哈希元数据被攻破，客户端代码供应链受影响 | 独立签名、固定公钥、回滚保护、透明日志 | `SOURCE_VERIFIED`（机制） |

## 中风险与隐私项

- 订阅、条目、AI 内容、规则和 MCP 配置经过远端 `api.folo.is`；数据保留、训练、地域和删除策略不在源码中，属 `UNVERIFIED`。
- AI 摘要、翻译、聊天和任务把阅读上下文交给服务端；没有在客户端看到完整 prompt-injection 边界或工具调用授权策略。
- MCP 连接 headers/配置通过远端 API 管理，服务端秘密存储与工具执行隔离不可见。
- 项目集成 PostHog、Sentry、Firebase/Push、ReCAPTCHA 等外部服务；具体启用条件、事件字段和生产隐私行为需运行与网络审计。
- `app://` 特权 scheme 注册了 `bypassCSP: true`，需要结合实际响应头和资源装载进行专项审查。
- 本地 SQLite/IndexedDB、localStorage 和 Electron Store 不是加密证据库；用户目录被读取时可能泄漏信息。
- 客户端缓存会删除旧条目/翻译，不适合审计、取证或长期研究复盘。

## 已有缓解措施

- Readability 内容使用 DOMPurify 清洗，并限制 iframe。
- 外部 HTTP(S) 链接通常交给系统浏览器；部分危险 scheme 有确认/拦截逻辑。
- OTA/hot updater 有内容 hash 校验，Electron Forge 配置使用 asar integrity/fuses。
- Vercel webhook 使用 HMAC-SHA1 验证签名后再清缓存。
- 代码中有 cookie 迁移/去重、响应头和单实例/深链处理。

这些是源码层积极信号，不等于漏洞已消除，也不是 `RUNTIME_VERIFIED`。

## 财经情报特有风险

1. 没有不可变来源原文、hash 和采集批次，无法证明摘要/判断对应哪个原始版本。
2. 后端去重、排序和推荐不透明，可能产生来源偏置或遗漏，无法本地复核。
3. AI 输出没有看到统一置信度、引用强制和事实校验数据模型。
4. 远端服务中断或条款变化会影响核心数据流。
5. 不应将任何 AI/Action 结果解释为自动交易信号；Folo 代码也不构成受控投资决策系统。

## 采用前最低整改门槛

若未来选择借鉴 Electron 实现，必须先完成窗口隔离、IPC allowlist、URL/SSRF 防护、密钥安全存储、外联清单、更新签名和证据库分离；未经整改不得把现有安全配置带入正式客户端。

