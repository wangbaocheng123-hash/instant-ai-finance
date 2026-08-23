# changedetection.io 配置体系

## 配置优先级

源码不是单一配置文件，而是多层合并：

1. CLI/环境变量控制进程、端口、datastore、worker 和安全开关；
2. `changedetection.json` 保存全局 application/requests/settings；
3. tag 可覆盖部分 watch 行为；
4. `{uuid}/watch.json` 保存单 watch 配置；
5. `{uuid}/<processor>.json` 保存 processor 私有配置；
6. headers 可由 watch、global settings、全局/单 watch `headers.txt` 合并。

通知明确按 watch→tag→global 解析；其他字段的 override chain 并不统一，`watch_base` 和 `Watch.model` 的源码文档将其列为待重构技术债。

## CLI 配置

证据：`changedetectionio/__init__.py::print_help/main`。

| 参数 | 作用 |
|---|---|
| `-h HOST` | 监听地址 |
| `-p PORT` | 端口，默认 5000 |
| `-d PATH` | datastore 目录 |
| `-C` | datastore 不存在时创建 |
| `-s` | SSL |
| `-l LEVEL` | 日志级别 |
| `-P true/false` | 全局 pause/unpause |
| `-u URL` / `-uN JSON` | 启动时添加 watch 及其选项 |
| `-r all|UUID... [N]` | 启动时重查并可重复 |
| `-b` | batch，无 HTTP server |

## 进程与调度环境变量

| 配置项 | 默认/来源 | 源码证据 |
|---|---|---|
| `LISTEN_HOST` | `0.0.0.0` | `changedetectionio/__init__.py::main` |
| `PORT` | `5000` | 同上 |
| `LOGGER_LEVEL` | 入口默认 `DEBUG` | 同上 |
| `FETCH_WORKERS` | 全局 requests worker 设置；模型默认 5 | `flask_app.changedetection_app`、`model/App.py` |
| `NOTIFICATION_WORKERS` | `1` | `flask_app.changedetection_app` |
| `MINIMUM_SECONDS_RECHECK_TIME` | `3` | `ticker_thread_check_time_launch_checks` |
| `WORKER_MAX_JOBS` | `10` | `worker.async_update_worker` |
| `WORKER_MAX_RUNTIME` | `3600` 秒 | 同上 |
| `TZ` | 日程 timezone fallback | `flask_app.ticker_thread_check_time_launch_checks` |
| `DISABLE_VERSION_CHECK` | 默认不禁用 | `flask_app.changedetection_app` |
| `SOCKETIO_MODE` | requirements 注释推荐 threading | `requirements.txt`/realtime 模块；实际运行未验证 |

## 抓取与浏览器配置

| 配置项 | 作用 | 源码证据 |
|---|---|---|
| `DEFAULT_FETCH_BACKEND` | 全局默认，默认 `html_requests` | `model/App.py::base_config` |
| `DEFAULT_SETTINGS_REQUESTS_TIMEOUT` | 默认 45 秒 | 同上 |
| `DEFAULT_SETTINGS_REQUESTS_WORKERS` | 默认 5 | 同上 |
| `DEFAULT_SETTINGS_HEADERS_USERAGENT` | requests 默认 UA | 同上 |
| `PLAYWRIGHT_DRIVER_URL` | 外部 CDP/Playwright browser endpoint | `content_fetchers/__init__.py`、`playwright.py` |
| `FAST_PUPPETEER_CHROME_FETCHER` | Playwright/Puppeteer 选择 | `content_fetchers/__init__.py` |
| `WEBDRIVER_URL` | Selenium endpoint | `content_fetchers/webdriver_selenium.py` |
| `HTTP_PROXY`/`HTTPS_PROXY` | 系统代理 | `content_fetchers/base.py` |
| `playwright_proxy_*` | Playwright proxy | `content_fetchers/playwright.py` |
| `SCREENSHOT_MAX_HEIGHT` 等 | 截图限制 | `content_fetchers/__init__.py`/截图实现 |
| `DISABLED_PROCESSORS` | 默认 `image_ssim_diff` | `processors/__init__.py::_available_processors_cached` |

## 安全相关配置

| 配置项 | 默认/风险 | 源码证据 |
|---|---|---|
| `ALLOW_FILE_URI` | 默认 false；开启允许本地文件读取 | `validate_url.is_fetch_url_allowed` |
| `ALLOW_IANA_RESTRICTED_ADDRESSES` | 默认 false；开启允许私网/保留地址，也影响 LLM endpoint | 同上、`is_llm_api_base_safe` |
| `SAFE_PROTOCOL_REGEX` | 可覆盖安全协议 allowlist；高风险 | `validate_url.is_safe_valid_url` |
| `BLOCK_SIMPLEHOSTS` | 控制简单 hostname | 同上 |
| `HISTORY_SNAPSHOT_FILE_ALLOW_OUTSIDE_WATCH_DATADIR` | 默认 false；开启会关闭历史路径限制 | `model/Watch.py::get_history_snapshot`、`flask_app.py` |
| `SALTED_PASS` | 外部登录密码 | `auth_decorator.py`/登录逻辑 |
| `HIDE_REFERER` | 可设置 same-origin referrer policy | `changedetectionio/__init__.py::hide_referrer` |
| `USE_X_SETTINGS` | 信任 `X-Forwarded-*` 一跳 | 同上 |
| `MAX_RESTORE_UPLOAD_MB`/`MAX_RESTORE_DECOMPRESSED_MB` | 备份恢复大小限制 | `blueprint/backups/restore.py` |

## LLM 配置

| 配置项 | 作用 | 源码证据 |
|---|---|---|
| `LLM_FEATURES_DISABLED` | 硬禁用 AI | `llm/evaluator.py::is_llm_features_disabled` |
| `LLM_MODEL`、`LLM_API_KEY`、`LLM_API_BASE` | 优先于 datastore | `llm/evaluator.py::get_llm_config` |
| `LLM_MAX_INPUT_CHARS` | 输入长度上限 | `llm/evaluator.py::_get_max_input_chars` |
| `LLM_TOKEN_BUDGET_MONTH` | 全局月度 token 上限 | `get_global_token_budget_month` |
| `LLM_TIMEOUT`、`LLM_LOCAL_TIMEOUT` | 云/本地 endpoint timeout | `llm/client.py`、`llm/evaluator.py` |
| datastore `application.llm` | enabled、provider、model、key、budget、prompt、counters | `model/LLMSettings.py::LLMSettings` |

API key 会保存于普通 JSON datastore 或环境变量中；因此部署必须把 datastore 和环境视为秘密边界，不得纳入本研究仓库。

## 全局与单 watch 关键项

- 全局默认：`model/App.py::model.base_config`，含 requests interval、timeout/workers、fetch backend、ignore/filter、history max、notification、RSS、password、UI、timezone。
- watch 默认：`model/__init__.py::watch_base.__init__`，含 URL、processor/fetch backend、headers/method、调度、过滤、通知、browser steps、LLM intent。
- processor 配置：`processors/__init__.py::save_processor_config` 限制 processor name 后写 `<processor>.json`。

## “即时 AI”配置建议

- 作为 sidecar 时，只在独立进程配置 datastore、loopback 监听、API token、最少 worker 和允许的 fetch backend。
- 不在 changedetection watch 中保存“即时 AI”的主密钥；LLM 若由“即时 AI”统一处理，应在 sidecar 关闭 LLM。
- `ALLOW_FILE_URI`、`ALLOW_IANA_RESTRICTED_ADDRESSES`、`SAFE_PROTOCOL_REGEX` 和历史目录越界开关保持默认关闭，除非专门 ADR 与威胁审查批准。

所有结论为 `SOURCE_VERIFIED`；运行时取值和实际配置未验证。项目/提交：`dgtlmoon/changedetection.io` / `fce24780e74199bf34c62a0d90188cc2fc12f061`。

