# OpenBB 最终静态评估

研究锚点：`OpenBB-finance/OpenBB`，提交 `3e071fcc2cd9f891cac6040ae60296dba76dab46`，`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

## 结论

推荐角色：`DATA_PROVIDER`  
静态评分：**69 / 100**  
推荐复用形态：优先 `SIDE_CAR_SERVICE` / `API_INTEGRATION`，其次受控 `LIBRARY_DEPENDENCY`；不建议 `FORK_CORE`。  
研究状态：核心源码链已完成静态考古；运行、上游活跃度、provider 条款和法律边界尚未验证。

OpenBB 是六仓中非常有价值的结构化金融数据层候选，但不是“即时 AI”情报主底座。它可以补齐行情、财报、宏观、利率、商品、监管披露和部分新闻数据；它不能直接补齐持续监控、跨源去重、证据库、事件分类、AI 筛选、摘要和推送。

## 统一评分

| 评价项目 | 得分 | 理由与证据 |
|---|---:|---|
| 与财经情报需求匹配度 | 14/20 | equity/economy/commodity/news/regulators 和 32 providers 高度相关；缺少完整情报工作流。证据：extensions、providers、`news_router.py`。 |
| 已有功能完整度 | 10/15 | Python/REST/CLI/MCP/desktop surfaces 完整；没有调度、去重、AI、通知和证据库。 |
| 代码可维护性 | 8/10 | Provider/Fetcher/standard model 边界清楚、类型和测试丰富；动态生成 package、仓库体量和 provider 差异增加复杂度。 |
| 扩展和适配能力 | 10/10 | 三类 entry points、认证扩展、output callback、Router、Provider、MCP/OpenAPI。证据：`ExtensionLoader`、`Router`。 |
| Windows 本地运行能力 | 7/10 | Python 路径不需 Docker/WSL，且有 Windows 桌面/NSIS；但未运行，桌面依赖重。 |
| 数据来源能力 | 10/10 | 32 provider 包、180 标准模型，覆盖官方/商业/第三方来源；实际可用性仍需 provider 逐项测试。 |
| AI 与过滤能力 | 3/10 | MCP 让外部智能体调用数据，`to_llm()` 可序列化；无内建 LLM 推理、AI筛选或重要度评分。 |
| 上游活跃度 | 2/5 | 快照内有版本 4.7.3、桌面 1.0.2 和大量 tests；无 `.git`，无法验证提交时间、release 或近期活跃度。 |
| 许可证适配性 | 1/5 | 根/核心/CLI/manifests 为 AGPL；桌面 MIT/AGPL 信号冲突，且 provider 数据条款需逐项审查。 |
| 改造成本 | 4/5 | 作为 API/library 数据源接入成本低；若当主底座则需补大量情报能力，成本高。本分按推荐角色计。 |
| **合计** | **69/100** | 静态评分；运行与法律验证后必须复评。 |

## 初始假设验证

假设：“OpenBB 可能适合作为行情和金融数据提供器。”  
结论：**静态源码支持该假设。**

证据链：

```text
extension route (例如 equity/news/commodity)
→ Query
→ QueryExecutor
→ Provider.fetcher_dict
→ Fetcher TET
→ 标准 Data
→ OBBject
→ Python/REST/MCP
```

代表源码：

- `core/openbb_core/app/router.py::Router.command`
- `core/openbb_core/app/query.py::Query.execute`
- `core/openbb_core/provider/query_executor.py::QueryExecutor.execute`
- `core/openbb_core/provider/abstract/fetcher.py::Fetcher.fetch_data`
- `providers/sec/openbb_sec/__init__.py::sec_provider`
- `providers/federal_reserve/openbb_federal_reserve/__init__.py::federal_reserve_provider`
- `providers/eia/openbb_us_eia/__init__.py::eia_provider`

验证状态：`SOURCE_VERIFIED`，尚非 `RUNTIME_VERIFIED`。

## 建议采用的范围

### 建议进入下一步验证

1. OpenBB 最小 Python/API sidecar。
2. SEC/官方宏观/商品 provider：SEC、FRED/FED、BLS、CFTC、EIA、government_us。
3. 免费或低门槛行情 provider 作为补充，不把单一非官方网页源当证据源。
4. 标准模型和 Provider/Fetcher 作为“即时 AI”适配器接口的设计参考。
5. MCP 放到 MVP 后；先证明数据和证据闭环，再开放智能体工具。

### 不建议采用

1. 不把完整 OpenBB Fork 为主底座。
2. 不复制 AGPL provider/standard model 源码到正式产品。
3. 不把 OpenBB cache 当长期数据库或证据库。
4. 不使用 OpenBB desktop 作为“即时 AI”最终 UI；它是环境/服务管理器，不是情报阅读器。
5. 不默认安装 `openbb[all]`、CLI、MCP、charting 和 desktop 全家桶。

## 建议集成边界

```text
即时 AI 核心
  ├─ 自有采集/网页变化/RSS
  ├─ 自有标准化、去重、实体、事件、AI、证据、数据库、推送
  └─ FinancialDataPort
        └─ OpenBB localhost adapter
              ├─ SEC/FRED/FED/CFTC/EIA
              ├─ 行情/财报 providers
              └─ 可选 MCP（后期）
```

所有 OpenBB 返回都应在边界处转成“即时 AI”自有 schema，并记录 provider、route、参数、抓取时间、原始 URL 和许可证/条款标签。

## 主要阻塞

- 正式 Git clone 未完成；官方归档无 `.git`。
- 未运行任何 Python/API/MCP/desktop 路径。
- 未核验目标中国市场、紫金矿业、黄金和铜相关 provider 的实际覆盖、时效和字段质量。
- AGPL 组合/分发边界和桌面许可证冲突未解决。
- provider 当前服务条款、速率和再分发权未核验。

## 下一项建议（本项目内，不替代总任务的唯一下一步）

在完成全仓第一轮静态研究后，由主任务决定是否申请一个**最小 OpenBB 运行验证**：隔离 venv、只装核心和 1–2 个 provider、cache/export 指向 H 盘临时子目录、启动 localhost API、记录下载量/磁盘/日志/响应 schema，再决定是否保留为数据 sidecar。未经批准不得执行。
