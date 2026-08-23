# “即时 AI”MVP 蓝图

状态：`LOCAL_MVP_USABLE / IMPLEMENTATION_CONTINUES`。正式实现和运行选择见 ADR-0004；本文件继续作为范围与验收蓝图。

## 产品边界

第一版只做电脑所有者本人的财经情报闭环：围绕 AI 产业链、紫金矿业、黄金、铜和有色金属，从少量高可信来源收集信息，保留证据，去重和评分，生成带引用摘要，供 Windows 本地阅读，并只推送真正重要的变化。系统不识别“用户”，所有正式数据天然属于本机所有者。

不做自动交易、自动买卖建议、付费墙绕过、全网爬虫、社交平台账号自动化、账户体系、个人资料、移动端、多人协作或云同步。

## 推荐技术形态

| 层 | MVP 候选 | R0 判断 |
|---|---|---|
| Windows 客户端 | localhost Web UI + Edge 应用壳 | MVP 已实现；未来可替换为独立打包壳 |
| 领域核心/API | Python 本地服务 | MVP 已实现，只监听 `127.0.0.1` |
| 元数据/检索 | SQLite WAL + FTS5 | MVP 已实现，迁移与备份必须版本化 |
| 原文/附件证据 | H 盘内容寻址文件 | 已实现，与数据库分离并以 SHA-256 定位 |
| Feed 转换 | RSSHub localhost sidecar | 白名单路由，非必需来源不启用 |
| 网页变化 | changedetection.io localhost sidecar | 只监测授权官方页面 |
| 结构化金融数据 | OpenBB 最小 Provider/API sidecar | 只装首批 Provider，逐项审数据条款 |
| 热点/规则样机 | TrendRadar 条件 sidecar/候选 Fork | 先通过依赖、运行、GPL 和数据质量闸门 |
| 工作流 | n8n 后置可选 | 不进入 MVP 默认安装和核心路径 |
| UI 设计参考 | Folo | 不复制源码、图标或不安全 Electron 配置 |

## H 盘数据布局

```text
H:\即时AI文件库\
  raw\              # 授权原始响应/附件，按内容 hash 保存
  evidence\         # 规范化证据清单、来源元数据和版本引用
  database\         # SQLite/索引候选，仅正式业务库
  cache\             # 可删除的 sidecar/provider 缓存
  exports\           # 人工导出和报告
  backups\           # 经校验的数据库/配置备份
  logs\              # 脱敏运行日志
```

密钥不进入 H 盘普通配置或 Git；候选方案是 Windows Credential Manager/DPAPI。上游 sidecar 使用各自隔离子目录，不能直接写 `database` 正式区。

## 推荐数据流

```text
Source registry
  -> adapter fetch/change/query
  -> Evidence envelope(URL, fetched_at, status, hash, raw locator, terms tag)
  -> deterministic normalization/dedup
  -> entity extraction(company/commodity/industry/policy)
  -> event classification
  -> explainable rule score
  -> optional AI classification/summary with evidence citations
  -> canonical database + full-text index
  -> inbox/timeline/detail/search
  -> notification outbox -> approved channel
```

AI 不负责决定是否保存原文；原始证据必须先落地。AI 结果必须记录模型、模板版本、证据 ID、时间和成本，本机所有者可以看到“为什么重要”。

## 第一批数据源

1. 紫金矿业官网、交易所/监管披露和官方公告页面：Feed 优先，无 Feed 才用 changedetection。
2. 中国证监会、上交所/港交所等与目标公司和行业相关的授权公告/Feed；RSSHub 中已定位的 CNInfo、SSE 等财经路由只能在逐路由核验后启用。
3. 与黄金、铜、宏观政策相关的政府、央行、统计/监管机构公开页面或 API。
4. OpenBB 首批只验证 SEC、FED/FRED、BLS、CFTC/EIA 等权威 Provider；它们是海外宏观/监管补充，不代替中国市场来源。
5. TrendRadar 热榜作为发现线索，必须回链到原始来源，不能直接成为最高可信证据。

每个源启用前记录授权/条款、更新频率、原始 URL、字段、速率限制、保存权限和失败策略。

## 第一批界面

- 今日重点：按规则分数和事件类型展示少量高价值条目。
- 时间线：未读/收藏/主题/来源过滤，参考 Folo 的信息密度而独立实现。
- 证据详情：原文 URL、抓取时间、来源级别、hash、版本差异、命中规则、AI 引用。
- 主题页：AI 产业链、紫金矿业、黄金、铜/有色四个预置视图。
- 搜索：标题、摘要、实体、事件和全文索引。
- 来源与任务：来源开关、最近成功/失败、下次运行、数据条款标签。
- 系统状态：H 盘路径、来源、模型、通知和 sidecar 运行状态；仅在相应能力首次启用时提供最小本机技术配置，不设“账户/个人设置”页面，密钥通过系统安全存储管理。

## 第一批确定性规则

- 公司/别名：紫金矿业及股票代码、主要子公司/项目名称。
- 商品/产业：gold/黄金、copper/铜、有色、冶炼、矿山、精矿、库存、产量、品位等词组。
- 事件：财报/业绩预告、重大合同、收购出售、停复产、事故、制裁、监管处罚、资本开支、产量指引、供需和库存变化。
- 来源权重：公司/监管/交易所 > 权威行业机构 > 专业媒体 > 热榜线索。
- 重复抑制：来源 ID/GUID → 规范 URL → 内容 hash → 标题相似簇；重复条目合并证据，不删除来源。
- 推送门槛：高可信来源 + 高影响事件 + 新信息；AI 分数不能单独触发。

## 实施顺序与验收

| 顺序 | 工作包 | 完成条件 |
|---:|---|---|
| 0 | D0 架构与许可证决策 | 批准组合边界；个人单机、无账户且当前不分发已经固定 |
| 1 | P0 存储/证据 spike | H 盘隔离库可初始化、备份/恢复；10 条样本保留 hash/URL/版本 |
| 2 | Adapter contract | mock/RSS/changedetection 三类输入转同一 Evidence envelope，失败可重试且幂等 |
| 3 | 首批权威来源 | 每类至少一个来源连续两次采集，无重复丢证据，条款记录齐全 |
| 4 | 规则/实体/事件 | 固定标注集上可解释，误报/漏报有人工复核记录 |
| 5 | AI 后处理 | 证据包/任务边界已实现；真实模型执行待安全配置，离线时不阻断入库和阅读 |
| 6 | 本地 API 与阅读 UI | 今日重点、时间线、详情、搜索、来源状态闭环可用 |
| 7 | 通知 outbox | 站内低噪声 outbox 已实现；Windows 系统通知待完成，无交易动作 |
| 8 | Windows 壳与安装 | 只监听 localhost，业务数据仅落 H 盘，升级/卸载不删除业务库 |
| 9 | 可选 Provider/工作流 | OpenBB/n8n 分别通过独立闸门，不影响核心启动 |

## 第一阶段明确不做

- 不全量接入 RSSHub 路由、OpenBB Provider 或 n8n 节点。
- 不把六仓任何数据库当正式主库。
- 不做向量数据库优先设计；先用可解释的确定性去重和全文检索。
- 不让 Agent/MCP 获得任意网络、文件、通知或配置修改权限。
- 不构建任何账户、个人资料、用户偏好中心、多用户权限、手机 App、实时行情终端或自动交易，也不为这些能力预留 schema/API。

## 实施批准与当前状态

1. 方案 B（薄型领域核心 + 可选 sidecars）已由用户批准并记入 ADR-0004。
2. 产品已固定为仅本机所有者个人使用；若未来另行提出分发/商业化，必须重新评估 GPL/AGPL/SUL 边界，不在当前架构预留。
3. TrendRadar 隔离依赖安装已获“其他任务继续执行”授权；随后项目仍按依赖规模逐项记录。
4. SQLite/FTS5 与桌面壳已完成第一版实现；核心采集、证据、去重、检索和阅读闭环可用。

`product/` 已进入持续实施。真实 AI 提供器、通知 outbox、恢复演练和可选 sidecar 仍按任务账本推进，不得阻断现有离线/无密钥能力。
