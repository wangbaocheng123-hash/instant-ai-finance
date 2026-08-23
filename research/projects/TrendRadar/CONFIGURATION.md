# TrendRadar 配置体系

项目/提交：TrendRadar / `8ee26026ba6c11dec41a95fb3895a7162876caa1`。来源为 `OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`，不算完成克隆。

## 加载规则

`trendradar/core/loader.py::load_config` 默认读取 `CONFIG_PATH` 或 `config/config.yaml`，然后依次加载 app、crawler、report、notification、schedule/timeline、weight、platform、RSS、AI、filter、display、storage 与 webhook。配置是 YAML 为底、部分环境变量覆盖；并非所有 YAML 项都支持环境变量。

验证：`SOURCE_VERIFIED`。

## 主配置分区

| 分区 | 关键作用/默认快照状态 |
|---|---|
| `app` | 时区 `Asia/Shanghai`、版本提示 |
| `schedule` | 默认 `enabled: false`，preset 为 `morning_evening` |
| `platforms` | 默认启用 11 个热榜源；可配置 `api_url` 和每源 `expected_domain` |
| `rss` | 默认启用；快照启用 Hacker News、Yahoo Finance，阮一峰源禁用 |
| `report` | 默认 current、keyword 分组、rank threshold 5 |
| `filter` | 默认 keyword；可切 AI |
| `ai_filter` | batch、兴趣文件、提示词、min score、重分类阈值 |
| `display` | 区域顺序/显示开关/独立展示源 |
| `notification` | 总开关与九类渠道；默认 URL/token 为空 |
| `storage` | auto；SQLite 必开，TXT 默认关、HTML 开，本地 `output` |
| `ai` | LiteLLM model/key/base/timeout/token/retry |
| `ai_analysis` | AI 报告开关、模式、新闻上限、RSS/排名/独立区包含策略 |
| `ai_translation` | 开关、目标语言、翻译范围 |
| `advanced` | 网络间隔、代理、批大小、权重、版本 URL 等 |

## 环境变量覆盖

源码明确支持的主要变量：

- 基础：`CONFIG_PATH`, `TIMEZONE`, `DEBUG`, `PLATFORMS_API_URL`, `FREQUENCY_WORDS_PATH`
- 报告/调度：`SORT_BY_POSITION_FIRST`, `MAX_NEWS_PER_KEYWORD`, `SCHEDULE_ENABLED`, `SCHEDULE_PRESET`
- AI：`AI_MODEL`, `AI_API_KEY`, `AI_API_BASE`, `AI_TIMEOUT`, `AI_ANALYSIS_ENABLED`, `AI_TRANSLATION_ENABLED`, `AI_TRANSLATION_LANGUAGE`, `AI_FILTER_ENABLED`
- 存储：`STORAGE_BACKEND`, `STORAGE_TXT_ENABLED`, `STORAGE_HTML_ENABLED`, `STORAGE_RETENTION_DAYS`, `LOCAL_RETENTION_DAYS`, `REMOTE_RETENTION_DAYS`, `PULL_ENABLED`, `PULL_DAYS`, `S3_*`
- 通知：`FEISHU_WEBHOOK_URL`, `DINGTALK_WEBHOOK_URL`, `WEWORK_*`, `TELEGRAM_*`, `EMAIL_*`, `NTFY_*`, `BARK_URL`, `SLACK_WEBHOOK_URL`, `GENERIC_WEBHOOK_*`, `MAX_ACCOUNTS_PER_CHANNEL`
- 容器：`RUN_MODE`, `CRON_SCHEDULE`, `IMMEDIATE_RUN`, `WEBSERVER_PORT`, `MCP_PORT`, `MCP_HOST`

环境变量名及优先级来自 `core/loader.py`、`storage/manager.py`、Docker 文件，状态 `SOURCE_VERIFIED`。

## 规则与提示词配置

- `core/frequency.py::load_frequency_words` 支持 `[GLOBAL_FILTER]`/`[WORD_GROUPS]`、`+必须词`、`!排除词`、`@上限`、`/regex/`、`=>显示名` 和组别名。
- 自定义关键词短文件从 `config/custom/keyword/` 查找；自定义兴趣文件由 `AIFilter.load_interests_content` 处理。
- `config/timeline.yaml` 定义 presets/custom，`Scheduler._validate_timeline` 检查 week map、引用、时间格式和重叠。

## 敏感配置建议

源码允许把 API key、S3 secret、邮件密码、bot token 和 webhook 写进 YAML，但“即时 AI”不得如此复用。应只通过进程环境或 Windows 凭据管理器注入，并确保 `H:\即时AI文件库` 中的日志不会打印秘密。`AIAnalyzer.analyze` 仅打印 API key 前五位加掩码；这仍会泄露 key 前缀，生产日志应完全不输出。

## 配置风险

- YAML 字段与运行时大写键经过手工映射，新增配置容易漏映射。
- `setup-windows.bat` 的错误提示写 Python 3.10+，但 `pyproject.toml` 要求 3.12+。
- `start-http.bat` 固定 `0.0.0.0`，没有本地-only 默认。
- `schedule.collect` 配置语义与实际调用时序不一致，不能按字面信任。
