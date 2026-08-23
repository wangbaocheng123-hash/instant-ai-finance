# RSSHub 最终静态评估

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`，不等于完成克隆）。

## 结论

推荐角色：`SIDE_CAR_SERVICE`。

RSSHub 是“即时 AI”最有价值的来源扩展服务候选之一，但不是主底座。它已经实现大量财经与
公告来源到标准 Feed 的适配，可显著减少重复写抓取器；同时没有正式历史库、跨源去重、实体
事件、评分、调度和主动通知，无法承担完整金融情报系统。

推荐组合边界：

```text
独立 RSSHub localhost 进程
  -> 即时 AI Feed Adapter
  -> H:\即时AI文件库 中的原文/证据/数据库
  -> 自有去重、实体、事件、评分、AI、通知和桌面 UI
```

该结论仅基于静态源码，仍需第二优先级的 Windows 运行验证和许可证审查才能进入总选型。

## 100 分制评分

| 评价项 | 分数 | 理由与证据 |
|---|---:|---|
| 与财经情报需求匹配度 | 16/20 | 巨潮、上交所、雪球、金十、财联社、东方财富、中国黄金协会等真实 Route；缺情报后处理 |
| 已有功能完整度 | 10/15 | 抓取、缓存、格式、API、监控完整；无历史库、调度、通知与事件分析 |
| 代码可维护性 | 8/10 | TypeScript、Hono、类型、自动 registry、测试齐全；Route 数量大且易受站点变化影响 |
| 扩展和适配能力 | 9/10 | Namespace/Route/Radar、构建期发现和 npm registerRoute；直接内嵌耦合较高 |
| Windows 本地运行能力 | 7/10 | Node scripts 静态上跨平台、Docker 非必需；未运行，浏览器和依赖成本未知 |
| 数据来源能力 | 10/10 | 来源层是项目核心，财经覆盖与扩展模式强 |
| AI 与过滤能力 | 5/10 | 通用正则、全文与可选 OpenAI；无结构化 AI 情报流水线和审计 |
| 上游活跃度 | 3/5 | 官方固定提交快照结构现代且测试完善；无 `.git`，无法从本子任务复核历史/频率 |
| 许可证适配性 | 2/5 | AGPL-3.0 可开源使用但网络 copyleft 影响修改版和内嵌；sidecar 较可控 |
| 改造成本 | 4/5 | 作为 sidecar 接口成本较低；逐 Route 验收和长期维护仍有成本 |
| **总分** | **74/100** | 静态评分；运行失败或许可证边界变化可下调 |

## 推荐采用

1. 原版或合规维护的 RSSHub 作为本机独立服务。
2. 首批只启用公开、授权、与研究主题直接相关的 Route：交易所/公告、公开财经快讯、黄金行业。
3. 用标准 RSS/Atom/JSON Feed 接入，下游保存 source route、guid、原文链接、抓取时间和证据。
4. 借鉴 Route metadata、两级缓存和 Adapter 隔离模式，自行设计“即时 AI”的数据接口。

## 不建议采用

- 不作为 `PRIMARY_BASE` 或桌面 UI 底座。
- 不把 npm 包直接嵌入闭源正式客户端，当前标记 `REJECT`。
- 不复制 Route/核心源码到产品目录。
- 不把 Redis/memory cache 当历史数据库。
- 不用请求参数的 `chatgpt` 取代正式 AI 分析、提示词审计与成本控制。
- 不默认启用任意域名、浏览器、Cookie 和付费来源。

## 关键证据索引

| 判断 | 项目/提交/源码/标识符 | 状态 |
|---|---|---|
| 请求驱动服务 | RSSHub @ `5151…d683`；`lib/index.ts::serve`；`registry-helpers.ts::wrappedHandler` | `SOURCE_VERIFIED` |
| 统一数据模型 | 同提交；`lib/types.ts::Data/DataItem/Route` | `SOURCE_VERIFIED` |
| 财经来源真实存在 | 同提交；`lib/routes/cninfo/sse/xueqiu/jin10/cls/eastmoney/cngold` handlers | `SOURCE_VERIFIED` |
| 无业务数据库 | 同提交；cache/log/compose 源码与目录结构 | `SOURCE_VERIFIED_ABSENCE_IN_CORE` |
| 无采集调度/MCP/webhook | 同提交；核心入口与精确源码搜索 | `SOURCE_VERIFIED_ABSENCE_IN_CORE` |
| OpenAI 是可选后处理 | 同提交；`lib/middleware/parameter.ts::getAiCompletion` | `SOURCE_VERIFIED` |
| AGPL-3.0 | 同提交；`LICENSE` 第 13 节、`package.json.license` | `SOURCE_VERIFIED` |
| Windows 可运行 | package scripts 与 Node engines 仅表明设计意图 | `UNVERIFIED_RUNTIME` |

## 最大风险

1. AGPL 与正式桌面产品分发/内嵌边界。
2. 第三方来源条款、Cookie、付费内容和反爬合规。
3. 默认监听所有网卡及用户可控 URL 带来的攻击面。
4. Route 随上游 API/HTML 变化失效。
5. 浏览器、Node 依赖和大量路由带来的供应链与资源成本。

## 未确认事项

- Windows 本机实际安装量、内存、启动时间和停止清理；
- 选定财经 Route 的当前成功率、内容质量、稳定 guid 与刷新延迟；
- 依赖许可证/SBOM 与漏洞扫描；
- sidecar 随桌面安装包分发的具体 AGPL 合规方案；
- 官方上游近期活跃度、标签和发布策略（快照无 Git 历史）。

## 下一步（供主任务汇总，不修改全局状态）

完成报告质量复核后，把 RSSHub 维持为 `STATIC_ANALYSIS_COMPLETE / RUNTIME_NOT_ATTEMPTED`。
待第一优先级项目运行尝试后，申请在 `experiments/RSSHub-lab` 进行最小 Node + memory cache
运行验证，不安装 Docker/WSL，不启用浏览器和站点凭据。
