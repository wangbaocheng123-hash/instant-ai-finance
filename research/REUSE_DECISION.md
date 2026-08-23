# R0 复用决策

状态：`RECOMMENDATION_READY / NOT_AUTHORIZED_FOR_IMPLEMENTATION`。

## 结论先行

六个上游项目中，**没有一个应在当前证据下直接定为 `PRIMARY_BASE`**。推荐主底座是“即时 AI 自有的薄型领域核心”，范围严格限定为六仓都没有完整提供的：来源与证据契约、长期数据模型、跨源幂等、财经实体/事件、可审计规则评分、本地 API 和通知 outbox。

这不是批准从零重写采集器、Feed 转换器、网页 diff、金融 Provider、工作流引擎或成熟 UI 组件。那些能力分别通过 sidecar、API、上游库或设计参考复用。

## 项目角色决策

| 项目 | 角色 | 建议复用方式 | 决策 |
|---|---|---|---|
| TrendRadar | `CORE_FORK_CANDIDATE` | `SIDE_CAR_SERVICE`、`REWRITE_FROM_PATTERN`；若接受 GPL 再评估 `FORK_CORE` | 保留为最接近主链的候选和研究样机，当前不升格主底座 |
| RSSHub | `SIDE_CAR_SERVICE` | `API_INTEGRATION` | localhost 白名单路由服务，输出标准 Feed/条目 |
| changedetection.io | `SIDE_CAR_SERVICE` | `API_INTEGRATION` | 监测没有可靠 Feed 的官方网页，输出变化事件/快照引用 |
| OpenBB | `DATA_PROVIDER` | `SIDE_CAR_SERVICE` / `API_INTEGRATION` | 最小 Provider 集补充行情、财报、宏观、监管数据 |
| Folo | `UI_REFERENCE` | `DESIGN_REFERENCE` / `REWRITE_FROM_PATTERN` | 参考阅读器、时间线、详情、搜索与桌面交互；不复制代码和资产 |
| n8n | `WORKFLOW_ENGINE` | `API_INTEGRATION` | MVP 后用户可选 sidecar；不 Fork、不嵌入、不随默认核心运行 |

## 直接复用、API 接入和自有实现边界

### 通过独立服务/API

- RSSHub：选择性路由转换，不开放任意代理能力。
- changedetection.io：watch 生命周期、抓取、diff 和历史；主系统只接收标准变化事件。
- OpenBB：只装首批所需的官方/低风险 Provider，并把结果映射到自有 schema。
- TrendRadar：若运行和许可证门通过，可作为热点/规则/通知 sidecar；HTTP MCP 默认只绑 localhost 并限制工具。
- n8n：仅在后期通过 Public API/Webhook 交换最小 schema，不允许直接写正式数据库。

### 从模式独立实现

- TrendRadar 的关键词 DSL、排名轨迹、AI 兴趣标签版本、通知分片。
- changedetection 的 checksum/历史版本思想和调度防重复思想。
- OpenBB 的 Provider/Fetcher TET 和标准模型边界。
- Folo 的三栏阅读、store/service/cache 分层、快捷键和桌面生命周期。
- n8n 的 execution lifecycle、幂等键、outbox/重试和 adapter 思想。

独立实现必须保留 clean-room 证据，不复制受 GPL/AGPL/SUL 保护的具体表达。

### 即时 AI 必须拥有

1. `Source`：来源授权、类型、可信级别、抓取策略和条款标签。
2. `Evidence`：原始 URL、抓取时间、HTTP 元数据、内容 hash、不可变原文/附件定位。
3. `CanonicalItem`：跨源规范化标题、正文引用、发布时间、语言和重复簇。
4. `Entity/Event`：公司、商品、产业链、政策、财报和事件关系。
5. `RuleDecision`：确定性筛选、重要度分解、命中依据和版本。
6. `AIResult`：模型、提示模板版本、输入证据、输出、成本、置信/人工状态。
7. `NotificationOutbox`：为何推送、何时发送、渠道结果和重试，不允许 AI 直接绕过规则发出交易指令。

## 明确拒绝

- 不把六仓源码混到 `product/`。
- 不把 TrendRadar 的每日 SQLite 直接定为长期主库，也不依赖公共 NewsNow 作为唯一证据源。
- 不把 RSSHub 当历史数据库、调度器或未经限制的 URL 代理。
- 不把 changedetection 的文件 datastore 当统一财经库。
- 不把 OpenBB 完整 Fork、全量 Provider 或 desktop 打进主产品。
- 不复制 Folo `icons/mgc`，不继承其 `sandbox:false`、`nodeIntegration:true`、`contextIsolation:false` 安全配置。
- 不把 n8n execution DB 当正式证据库，不启用 `.ee`、Code、任意文件/网络或社区节点作为默认能力。
- 不绕过付费墙、不抓取未授权内容、不接入自动交易或生成自动买卖指令。

## 推荐的第一批组合

```text
权威/授权来源
  -> RSSHub（适合 Feed 化的来源）
  -> changedetection（无 Feed 的官方网页）
  -> OpenBB（结构化金融/监管数据）
  -> TrendRadar（热点补充，运行验证通过后）
  -> 即时 AI Evidence Intake
  -> 确定性去重/实体/事件/规则评分
  -> 可选 AI 摘要与分类
  -> H 盘正式库
  -> Windows 阅读器 + 可审计通知
```

首批主题固定为 AI 产业链、紫金矿业、黄金、铜与有色金属；首批来源优先公司/交易所/监管/政府和具有明确授权的 Feed/API，热榜只作发现线索而非最终事实依据。

## 尚未确认

- 六仓正式 Git 浅克隆尚未完成；官方归档无历史。
- TrendRadar 依赖尚未获批安装，因此只有失败启动尝试。
- changedetection、RSSHub、OpenBB 的最小 Windows 运行尚未进行。
- 产品发布/商业模型和 GPL/AGPL 接受度未决定。
- 桌面壳与数据库仍是候选，不在 R0 锁定。
- 首批数据源条款、速率、字段质量和长期存档权需逐源核验。

## 决策请求

建议用户在 D0 选择：是否批准“方案 B：薄型领域核心 + sidecar”进入分阶段实施设计；是否批准下一轮只在隔离目录安装 TrendRadar 锁定依赖并继续运行验证。未经批准，本文件不得被解释为已经开始产品开发。
