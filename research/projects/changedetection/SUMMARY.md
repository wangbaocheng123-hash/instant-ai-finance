# changedetection.io 静态研究摘要

## 研究基线

- 项目：`dgtlmoon/changedetection.io`
- 固定提交：`fce24780e74199bf34c62a0d90188cc2fc12f061`
- 默认分支：`master`
- 源码位置：`upstream/changedetection-snapshot`
- 取得方式：`OFFICIAL_ARCHIVE_SNAPSHOT`
- 下载日期：`2026-08-23`（来自 `UPSTREAM_LOCK.yaml`）
- 快照状态：目录无 `.git`；这是官方固定提交归档快照，**不能算 Git 克隆完成**，不能由本目录验证提交历史、标签指向或上游活跃度。
- 源码内版本：`0.55.8`，证据为 `changedetectionio/__init__.py::__version__`。
- 当前 tag：无法从无 `.git` 的归档快照验证；`0.55.8` 只是源码版本字符串，不等同于已验证 tag。
- 主要语言/技术：Python；Web UI 使用 Flask/Jinja 模板并含 JavaScript/CSS 静态资源。
- 许可证：根 `LICENSE` 为 Apache-2.0；并存的商业 hosting 文档需澄清，详见 `LICENSE_NOTES.md`。
- 上游活跃度：本离线快照无法验证，标记 `UNVERIFIED`。
- Windows 说明：源码有原生 Windows datastore 分支，README-pip 有 pip 启动命令；实际 Windows 启动未验证，详见 `WINDOWS_RUNBOOK.md`。
- 分析方式：仅静态阅读；未安装依赖、未启动项目、未发出监控请求。

## 项目实际解决的问题

changedetection.io 是一个自托管网页变化监控服务。它把 URL 注册为 watch，按全局或单 watch 时间间隔调度，使用普通 HTTP 或外部浏览器抓取内容，经选择器、文本规则、条件插件和 processor 处理后比较内容指纹，保存每次有效快照及历史索引，再通过 Web UI、REST API、RSS、Socket.IO 和 Apprise 通知暴露结果。

这不是财经新闻聚合器，也没有公司/商品实体模型、财经事件本体、跨来源新闻去重或行情数据库。其合适边界是“权威网页变化监控 sidecar”，例如监控交易所公告页、上市公司 IR 页面、政府政策页和机构报告索引页，再由“即时 AI”的标准化/实体识别/重要度评分链路继续处理。

## 目标用户与核心功能

目标用户是需要自托管监测网页、JSON API、RSS、PDF、商品库存/价格和可视变化的个人或团队。源码确认的核心能力包括：

1. 周期调度、队列及并发 worker；
2. `requests`、Playwright/Puppeteer/Selenium 和插件 fetcher；
3. CSS、XPath、JSONPath/jq、正则、忽略文本、触发文本和条件规则；
4. 文本/JSON/PDF、库存价格、截图 SSIM processor；
5. 文件型原子持久化、版本快照、历史裁剪和备份恢复；
6. REST API、RSS 输出、Web UI 和实时 Socket.IO 状态；
7. Apprise 多渠道通知；
8. 可选 LLM 意图过滤、变化摘要、token 预算与缓存。

## 与“即时 AI”的重合度

结论：**监控子链路重合度高，完整财经情报系统重合度中等偏低。**

- 高重合：权威网页定时监测、页面局部提取、差异检测、原始链接、历史证据、通知、API 接入。
- 部分重合：RSS/JSON/PDF 接入、AI 变化筛选与摘要、价格变化检测。
- 不重合：财经源目录、跨来源规范化与去重、公司/商品/产业实体识别、财经事件分类、投资研究重要度评分、结构化行情与宏观数据、桌面壳。

## 最强的五项能力

1. **成熟的网页变化闭环**：调度、抓取、过滤、判变、历史、通知由同一 watch 模型贯通。
2. **网页抓取后端分层**：普通 HTTP 是低成本默认；JS 页面可切外部浏览器，并支持 browser steps。
3. **低噪声过滤**：CSS/XPath/JSON、忽略/触发文本、条件规则、唯一行检查和可选 LLM 意图过滤叠加。
4. **证据留存**：每个 watch 独立目录保存配置、历史索引、内容快照、截图、XPath 数据和 AI 摘要缓存。
5. **清晰的服务集成面**：REST API、RSS、Apprise 出站通知、Pluggy fetcher/processor/hooks。

## 最大的五项问题

1. **不具备财经领域数据模型**：watch 以 URL/文本差异为中心，不能直接替代财经情报核心。
2. **存储与领域模型耦合**：`watch_base`/`Watch.model` 继承 `dict` 并直接持久化文件；源码自身标注这是技术债。
3. **进程内全局状态和线程较多**：Flask app、datastore、队列、ticker、notification runner、worker pool 共享状态；单 datastore 明确不支持多进程并发。
4. **Windows 的完整浏览器能力不是纯本机闭环**：基础 HTTP 可直接运行，但 JS 抓取默认依赖外部 Playwright/Selenium endpoint；`jq` 依赖在 Windows 未由 requirements 自动安装。
5. **许可证材料存在需澄清的并列表述**：根 `LICENSE` 是 Apache-2.0，而 `COMMERCIAL_LICENCE.md` 又声称商业 hosting 必须签署商业许可；二者关系需上游或法律专业人士确认。

## 关键源码证据

| 项目 | 提交 | 源码路径 | 类/函数/配置 | 结论 | 状态 |
|---|---|---|---|---|---|
| changedetection.io | `fce2478...f061` | `changedetectionio/flask_app.py` | `ticker_thread_check_time_launch_checks` | watch 按到期时间、日程、jitter、proxy reuse 约束进入优先队列 | `SOURCE_VERIFIED` |
| changedetection.io | `fce2478...f061` | `changedetectionio/worker.py` | `async_update_worker` | worker 串联 processor、抓取、判变、LLM、历史和通知 | `SOURCE_VERIFIED` |
| changedetection.io | `fce2478...f061` | `changedetectionio/processors/text_json_diff/processor.py` | `perform_site_check.run_changedetection` | 默认 processor 完成预处理、过滤、MD5 判变和唯一行检查 | `SOURCE_VERIFIED` |
| changedetection.io | `fce2478...f061` | `changedetectionio/model/Watch.py` | `save_history_blob`、`get_history_snapshot` | 每个 watch 保存历史索引及文本/二进制快照，并限制读取路径 | `SOURCE_VERIFIED` |
| changedetection.io | `fce2478...f061` | `changedetectionio/notification_service.py`、`changedetectionio/notification/handler.py` | `NotificationService.send_content_changed_notification`、`process_notification` | 通知按 watch→tag→global 解析配置，经队列与 Apprise 发送 | `SOURCE_VERIFIED` |
| changedetection.io | `fce2478...f061` | `changedetectionio/llm/evaluator.py` | `evaluate_change`、`summarise_change` | 可选 LLM 可抑制不重要变化并生成摘要，含全局/月度预算 | `SOURCE_VERIFIED` |

## 初步定位

- 推荐角色：`SIDE_CAR_SERVICE`
- 推荐接入：`API_INTEGRATION`
- 不建议：将其完整 Fork 为“即时 AI”主底座，或复制内部线程/文件存储实现到正式核心。
- 理由：它对网页变化监控成熟且边界清晰，但对财经领域建模和多源情报整合缺失；独立进程还能隔离抓取依赖、许可证、故障和数据迁移风险。
