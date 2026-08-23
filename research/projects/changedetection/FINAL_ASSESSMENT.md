# changedetection.io 最终静态评估

## 结论

- 推荐角色：`SIDE_CAR_SERVICE`
- 推荐复用方式：`API_INTEGRATION`
- 静态评分：**75/100**
- 阶段结论：已完成固定归档快照的核心链路静态考古；未完成 Git 克隆、运行验证、依赖/许可证全量扫描。

changedetection.io 很适合成为“即时 AI”的权威网页变化监控候选独立服务，但不适合作为财经情报主底座。服务边界应以 REST API 为主、出站 HTTP notification/RSS 为辅；“即时 AI”继续负责跨源去重、实体、事件、重要度、长期研究数据库和桌面体验。

## 100 分制

| 评价项目 | 得分 | 满分 | 理由与证据 |
|---|---:|---:|---|
| 与财经情报需求匹配度 | 16 | 20 | 权威公告/政策/IR 页面监控、证据历史和通知高度相关；无财经实体/事件/行情模型。证据：`worker.py`、`Watch.py`、`notification_service.py` |
| 已有功能完整度 | 13 | 15 | 调度、fetcher、过滤、diff、history、UI、API、RSS、通知、LLM 闭环完整；不是全情报平台。证据：`flask_app.py::changedetection_app` |
| 代码可维护性 | 7 | 10 | processor/fetcher/plugin 分层和测试资产较好；全局 app/datastore/thread、dict 模型和手工 override chain 是明显技术债。证据：`model/__init__.py::watch_base` |
| 扩展和适配能力 | 9 | 10 | Pluggy fetcher/processor/hooks、OpenAPI、RSS、Apprise；数据库 backend 实际仍只有文件。证据：`pluggy_interface.py`、`api/` |
| Windows 本地运行能力 | 7 | 10 | 源码含 `%APPDATA%` 路径、Python >=3.10、tzdata；完整 JS/PDF/jq 能力有外部依赖且未运行。证据：入口、requirements、Dockerfile |
| 数据来源能力 | 6 | 10 | 可监控 URL/RSS/JSON/PDF/JS 页面，但无财经 provider 目录、广域抓取与来源规范化。证据：`content_fetchers/`、`text_json_diff` |
| AI 与过滤能力 | 8 | 10 | CSS/XPath/JSON/规则/conditions/LLM intent+summary+budget 强；缺财经分类、实体和投资重要度模型。证据：processor、conditions、llm |
| 上游活跃度 | 2 | 5 | 无 `.git` 的归档快照无法验证提交频率、release/tag 和维护响应；版本 `0.55.8` 仅证明源码标识。状态：`UNVERIFIED` |
| 许可证适配性 | 3 | 5 | 根 LICENSE 是 Apache-2.0，但商业 hosting 文档产生必须澄清的并列表述，依赖许可证未扫描。证据：`LICENSE`、`COMMERCIAL_LICENCE.md` |
| 改造成本 | 4 | 5 | 以 sidecar/API 接入成本较低；若 Fork/嵌入则内部耦合、browser 和 datastore 改造成本高。证据：API 与模块图 |
| **合计** | **75** | **100** | 静态评分，待运行和许可证复核后调整 |

## 已验证假设

初始假设“changedetection.io 可能适合作为独立网页变化监控服务”得到源码支持：

- 独立服务入口和 datastore 完整；
- REST/RSS/notification 提供清晰边界；
- 核心链路不要求“即时 AI”导入其 Python 内部模块；
- 财经领域能力缺失，反而支持保持 sidecar 边界。

验证状态：`SOURCE_VERIFIED`；实际稳定性：`UNVERIFIED`。

## 采用建议

### 建议采用

- 用户指定的权威 URL/公告页变化监控；
- CSS/XPath/JSON 局部区域监控；
- 历史 before/after 证据；
- REST 创建/recheck/history/diff；
- 本地 HTTP notification 触发“即时 AI”拉取；
- 必要时 RSS 作为补偿轮询。

### 只借鉴

- scheduler + priority queue + UUID claim；
- filter pipeline；
- per-watch history 与 atomic write；
- token budget 和 fail-open AI filter；
- SSRF/path traversal 防线。

### 不建议

- 作为 `PRIMARY_BASE` 或 `CORE_FORK_CANDIDATE`；
- 直接把 `flask_app.py`、`worker.py`、Watch store 复制进正式产品；
- 用其 file datastore 存所有财经数据；
- 依赖 sidecar 的 LLM 作为唯一 AI 判断；
- 在许可证/运行未验证前对外 hosting 或分发。

## 集成边界

```text
changedetection.io（独立进程、独立 datastore、loopback）
       │ REST API + 本地 HTTP event + RSS fallback
       ▼
即时 AI changedetection adapter
       │ canonical event + source URL + before/after evidence
       ▼
即时 AI 主数据链：去重 → 实体 → 事件 → 重要度 → 摘要 → 桌面展示
```

## 未决项

1. Windows 本地 venv 的依赖安装/启动/空间/内存；
2. 基础 HTTP、API、RSS、notification 的端到端运行；
3. JS browser service 是否值得部署，及 Docker/WSL 成本；
4. `COMMERCIAL_LICENCE.md` 与 Apache-2.0 的关系；
5. 依赖 SBOM、第三方资产和插件许可证；
6. 实际 API adapter 的幂等、错误恢复、版本兼容；
7. sidecar datastore 在 H 盘的最终子目录，需未来 ADR/用户批准。

## 证据完整性声明

所有源码结论对应 `dgtlmoon/changedetection.io` 固定提交 `fce24780e74199bf34c62a0d90188cc2fc12f061`。研究对象是 `OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`，因此不能算 Git clone 完成，也不能由本地验证历史、tag 或活跃度。运行结果均未尝试，详见 `TEST_RESULTS.md`。

