# changedetection.io 核心模块地图

| 模块/目录 | 作用 | 实际入口 | 主要依赖 | 独立性 | 复用判断 | 难度 | 许可证影响 |
|---|---|---|---|---|---|---|---|
| `changedetectionio/__init__.py` | CLI、store/app/server 装配 | `main` | store、flask_app、worker_pool | 低 | 不直接复用；作为部署入口参考 | 中 | Apache-2.0 主体；商业 hosting 文档待澄清 |
| `flask_app.py` | Flask、API、blueprint、ticker、通知线程装配 | `changedetection_app` | Flask、队列、worker_pool、datastore | 低 | 不嵌入；服务运行边界 | 高 | 同上 |
| `worker_pool.py` | worker 生命周期、UUID claim、健康检查 | `start_workers` | asyncio、threading、worker | 中 | `DESIGN_REFERENCE` | 中 | 复制实现须履行 Apache 条款并审查 hosting 文档 |
| `worker.py` | 单 watch 核心处理链 | `async_update_worker` | processors、fetchers、LLM、store、notification | 低 | `DESIGN_REFERENCE` | 高 | 与内部模型强耦合 |
| `queue_handlers.py` | recheck/notification queue 封装 | `RecheckPriorityQueue`、`NotificationQueue` | `queue`、锁 | 中 | `DESIGN_REFERENCE` | 中 | 同上 |
| `content_fetchers/` | requests/浏览器抓取与 backend 解析 | `resolve_content_fetcher`、各 `fetcher.run` | requests、Playwright/Pyppeteer/Selenium | 中 | 服务内使用；外部只走 API | 高 | 外部依赖另有许可证 |
| `processors/` | processor 动态发现与配置 | `get_processor_module`、`find_processors` | Pluggy、模块导入 | 中 | `DESIGN_REFERENCE` 或插件扩展 | 中 | 插件和主体许可证分别审查 |
| `processors/text_json_diff/` | 默认文本/JSON/PDF 过滤和 MD5 判变 | `perform_site_check.run_changedetection` | html_tools、conditions、diff | 中 | 可借鉴过滤管线；优先保持服务内 | 中 | 若复制代码需 Apache 归属 |
| `processors/restock_diff/` | 商品 metadata/价格/库存变化 | `perform_site_check.run_changedetection` | extruct、price parser、可选 LLM plugin | 中 | 对黄金/铜价格页面仅作模式参考，不当行情源 | 高 | 同上 |
| `processors/image_ssim_diff/` | 截图视觉差异 | `perform_site_check.run_changedetection` | OpenCV/图像 fallback | 中 | 可选服务能力 | 高 | 需再审第三方图像依赖 |
| `conditions/` | JSON Logic 条件与 Pluggy operator | `execute_ruleset_against_all_plugins` | panzi-json-logic、Pluggy | 中高 | `DESIGN_REFERENCE` | 中 | 同上 |
| `store/` | 文件型 datastore、migration、原子提交 | `ChangeDetectionStore` | JSON/orjson、filesystem | 低 | 不作为“即时 AI”主数据库；服务私有存储 | 中 | 同上 |
| `model/Watch.py`、`model/__init__.py` | dict 型 watch 领域对象与历史文件 | `Watch.model`、`watch_base` | store、filesystem | 低 | 仅理解 API schema；不移植 | 高 | 同上 |
| `notification_service.py` | 通知上下文、级联配置、入队 | `NotificationService` | Watch、diff、queue | 中 | 保持 sidecar 内使用 | 中 | 同上 |
| `notification/handler.py` | Jinja 格式处理与 Apprise 发送 | `process_notification` | Apprise、Jinja | 中 | 出站通知可直接由服务使用 | 中 | Apprise/自定义插件许可证需一并审查 |
| `llm/` | LiteLLM provider、意图过滤、摘要、预算 | `evaluate_change`、`summarise_change` | LiteLLM、Pydantic、rank-bm25 | 中 | 不作为“即时 AI”总 AI 层；可作 change prefilter | 中 | 云服务条款/密钥/费用另行处理 |
| `api/` | REST 资源、token、OpenAPI validation | `Watch`、`CreateWatch` 等 | Flask-RESTful、OpenAPI Core | 高 | `API_INTEGRATION` 首选边界 | 低 | 服务分离降低代码混合风险 |
| `blueprint/rss/` | 变化 RSS 输出 | `construct_blueprint` | feedgen、notification context | 高 | `API_INTEGRATION` 备选读取边界 | 低 | 同上 |
| `blueprint/ui/`、`templates/` | Web 控制台、preview/diff/edit | blueprint routes | Flask/Jinja/前端静态资源 | 低 | `UI_REFERENCE`，不作为最终桌面 UI | 中 | 复制 UI 资源仍需许可证/归属 |
| `pluggy_interface.py` | fetcher/processor/UI/hook 插件系统 | `plugin_manager`、hookspec | Pluggy、setuptools entry points | 高 | 扩展 sidecar 的首选方式之一 | 中 | 插件自身许可证必须独立追踪 |
| `validate_url.py` | URL scheme、SSRF、parser differential 安全门 | `is_fetch_url_allowed` | DNS、validators、urllib3 | 中高 | `DESIGN_REFERENCE`，接入层仍需自身 SSRF 防护 | 中 | 同上 |
| `realtime/` | Socket.IO 事件推送 | `init_socketio` | Flask-SocketIO | 中 | 非首选集成边界 | 中 | 第三方依赖另审 |

## 模块耦合判断

### 边界清晰、适合服务化复用

- REST API/OpenAPI；
- RSS feed；
- Apprise 出站通知；
- 外部 Pluggy package entry point；
- 独立 datastore 目录。

### 不适合直接抽取

- `worker.py` 依赖 watch dict、datastore、processor、fetcher、signal 和 notification；
- `flask_app.py` 以全局 app/datastore/queue 装配大量线程；
- `Watch` 同时承担领域模型、配置解析、历史索引和文件 I/O；
- filter override chain 分散于 watch/tag/global，多处源码注释承认技术债。

## 证据

- Processor 插件：`processors/__init__.py::find_processors/get_processor_module`。
- Fetcher 插件：`content_fetchers/__init__.py::get_plugin_fetchers/resolve_content_fetcher`。
- Pluggy hooks：`pluggy_interface.py::ChangeDetectionSpec`。
- 存储耦合与未来抽象说明：`model/__init__.py::watch_base`、`model/Watch.py::model` 文档字符串。
- 多进程限制：`store/file_saving_datastore.py::save_json_atomic` 文档明确“Multi-process safety: Not supported”。

验证状态均为 `SOURCE_VERIFIED`，基线为 `fce24780e74199bf34c62a0d90188cc2fc12f061`。

