# 集成方案对比

状态：`R0_RECOMMENDATION_READY / USER_DECISION_REQUIRED`。工作量和维护成本为相对估计，不是排期承诺。

## 方案 A：TrendRadar 主底座

```text
TrendRadar Fork/核心
  + RSSHub Feed sidecar
  + changedetection 网页变化 sidecar
  + OpenBB 金融数据 adapter
  + 自建 Windows 桌面壳与证据扩展
```

优点：最快获得热榜/RSS、关键词、AI、静态报告、通知和 MCP 主链；Python/SQLite 对 Windows 本地原型友好；TrendRadar 静态评分最高。

缺点：GPL-3.0 会直接影响主程序发布模型；`NewsAnalyzer` 编排集中，默认公共 NewsNow 是单点依赖，`collect=false` 时序存在源码缺陷；每日标题库缺少长期原文证据、实体、事件、全文检索和迁移模型。为了达到目标，最终仍需大幅改造存储、采集可信度和桌面交互。

| 维度 | 判断 |
|---|---|
| 开发工作量 | 中 |
| 长期维护 | 中高；需持续跟随上游并维护深补丁 |
| 许可证风险 | 高；除非明确接受 GPL 发布模型 |
| Windows 难度 | 中；Python 可原生，运行依赖尚未验证 |
| 扩展能力 | 中高，但需增加正式 adapter/plugin 边界 |
| 适用条件 | 当前个人本机使用边界已满足一项前提，但仍需完整运行/数据验证通过 |

结论：保留为**条件备选**，当前不批准为 `PRIMARY_BASE`。

## 方案 B：自建薄型领域核心 + 成熟独立服务（推荐）

```text
即时 AI 自有薄核心：evidence + canonical item + entity/event + rule score + outbox
  <- RSSHub：Feed 转换
  <- changedetection：官方网页变化
  <- OpenBB：结构化金融数据
  <- TrendRadar：热点/规则/通知样机或可替换 sidecar
  <- n8n：MVP 后可选外围自动化
UI 参考 Folo，但不复制 Folo 代码/资产
```

优点：正式证据、长期数据和业务规则由产品掌控；上游升级或替换不会破坏核心 schema；许可证与进程边界清晰；只重写六仓都没有的领域能力，仍遵守复用优先；能把所有业务数据统一落到 H 盘。

缺点：需要自己设计 adapter contract、证据模型、跨源幂等、实体/事件和本地 API；早期可见功能比直接 Fork 少；必须避免范围膨胀或重新制造 RSS 路由、网页 diff、金融 Provider、通用 workflow 等成熟能力。

| 维度 | 判断 |
|---|---|
| 开发工作量 | 中高，但集中于不可替代的领域层 |
| 长期维护 | 中；sidecar 可独立升级/替换 |
| 许可证风险 | 中；边界清楚但仍需逐组件/分发场景审查 |
| Windows 难度 | 中；多个本地进程需生命周期管理 |
| 扩展能力 | 高；标准 adapter + 自有 schema |
| 适用条件 | 需要长期证据、可审计 AI、可替换来源和未来产品化 |

结论：**R0 推荐方案**。它不是从零重写成熟项目，而是建立最小领域核心并复用各仓最强边界。

## 方案 C：n8n 工作流平台主导

```text
n8n workflow/DB/UI
  -> RSS/HTTP/OpenBB/changedetection/AI/通知节点
  -> 少量自定义即时 AI 节点
```

优点：流程可视化、连接器、调度、Webhook、AI 和通知齐全；适合快速试验不同来源和自动化。

缺点：没有财经证据、实体/事件和可信来源模型；execution log 不等于业务数据库；通用 HTTP/Code/community nodes 扩大 SSRF、凭据和供应链风险；Sustainable Use + `.ee` Enterprise 分区对嵌入、分发和商业化不利；Windows 安装/资源成本最大。

| 维度 | 判断 |
|---|---|
| 开发工作量 | 原型低，达成可审计产品则高 |
| 长期维护 | 高；工作流漂移、节点升级和平台运维 |
| 许可证风险 | 高 |
| Windows 难度 | 高；大型 Node 依赖，queue 还需 Redis/Postgres |
| 扩展能力 | 很高但缺领域约束 |
| 适用条件 | 高级用户外围自动化，而非正式核心 |

结论：拒绝作为主导架构；保留为 MVP 后可选 `WORKFLOW_ENGINE`。

## 总比较

| 方案 | 首次价值 | 证据/数据主权 | 许可证可控性 | 长期可维护 | 推荐 |
|---|---|---|---|---|---|
| A TrendRadar 主底座 | 高 | 中低 | 低 | 中 | 条件备选 |
| B 薄型核心 + sidecars | 中 | 高 | 中高 | 高 | **推荐** |
| C n8n 主导 | 高（原型） | 低 | 低 | 低 | 不推荐 |

## 推荐决策门

D0 应批准的是方案 B 的**实施蓝图与边界**，不是直接开始全量编码。个人单机、无账户且当前不分发的边界已经固定；进入 P0 前仍需决定首批 sidecar 的运行安装、TrendRadar 技术验证、桌面壳对比试验和数据库候选验证。未来若改为向他人分发，再单独重开许可证决策。
