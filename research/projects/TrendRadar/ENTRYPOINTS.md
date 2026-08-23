# TrendRadar 入口清单

项目：TrendRadar；提交：`8ee26026ba6c11dec41a95fb3895a7162876caa1`；来源：`OFFICIAL_ARCHIVE_SNAPSHOT`。以下入口均 `SOURCE_VERIFIED`。

| 类型 | 源码路径与符号 | 启动/用途 |
|---|---|---|
| Python 模块主入口 | `trendradar/__main__.py::main` | `python -m trendradar`；支持 `--show-schedule`、`--doctor`、`--test-notification` |
| 安装后 CLI | `pyproject.toml [project.scripts]` | `trendradar = trendradar.__main__:main` |
| 主业务调用 | `trendradar/__main__.py::NewsAnalyzer.run` | 热榜 → RSS → 模式策略 → 清理 |
| MCP 模块入口 | `mcp_server/server.py::__main__`, `run_server` | `python -m mcp_server.server [--transport stdio|http]` |
| 安装后 MCP CLI | `pyproject.toml [project.scripts]` | `trendradar-mcp = mcp_server.server:run_server` |
| MCP stdio | `mcp_server/server.py::run_server` | `mcp.run(transport='stdio')` |
| MCP HTTP | `mcp_server/server.py::run_server` | 默认 `0.0.0.0:3333/mcp` |
| Windows MCP 安装脚本 | `setup-windows.bat` | 检查 Python/uv、可能自动安装 uv、执行 `uv sync`；本研究未运行 |
| Windows HTTP 脚本 | `start-http.bat` | `uv run python -m mcp_server.server --transport http --host 0.0.0.0 --port 3333` |
| Docker 主入口 | `docker/Dockerfile` + `docker/entrypoint.sh` | `RUN_MODE=once` 单次；`cron` 立即运行、启动静态 Web 和 supercronic |
| Docker MCP 入口 | `docker/Dockerfile.mcp` | HTTP MCP，端口由 `MCP_PORT` 控制 |
| Docker 管理入口 | `docker/manage.py::main` | run/status/config/files/logs/start/stop/status webserver |
| 静态 Web 入口 | `docker/manage.py::start_webserver` | `python -m http.server` 托管 `/app/output`；默认 8080 |
| GitHub 定时入口 | `.github/workflows/crawler.yml` | 定时/手工 workflow，安装 uv 依赖后运行 `uv run python -m trendradar` |

## MCP 暴露面

`mcp_server/server.py` 注册 4 个 resources 与 27 个工具（启动日志编号 0–26）。实际能力包括日期解析、新闻/RSS 查询、搜索、趋势/情感/聚合分析、配置状态、手动抓取、远程同步、文章读取和通知发送。关键变更型工具是：

- `trigger_crawl` → `SystemManagementTools.trigger_crawl`，即使 `save_to_local=False` 也会调用 `storage.save_news_data` 写 SQLite；该参数只控制额外 TXT/HTML。
- `sync_from_remote` → 下载远程 SQLite。
- `send_notification` → 对外发送消息。

因此 MCP 不是纯只读查询接口，部署时必须做访问控制。

## 关键配置入口

| 文件 | 作用 |
|---|---|
| `config/config.yaml` | 主配置：数据源、报告、筛选、通知、存储、AI、advanced |
| `config/timeline.yaml` | 调度预设/custom 时间线 |
| `config/frequency_words.txt` | 默认关键词规则 |
| `config/ai_interests.txt` | AI 筛选兴趣描述 |
| `config/ai_analysis_prompt.txt` | AI 分析提示词 |
| `config/ai_translation_prompt.txt` | 翻译提示词 |
| `config/ai_filter/*.txt` | 标签提取、更新与分类提示词 |
| `docker/.env` | Docker 环境模板；敏感值不应提交到“即时 AI”仓库 |
| `pyproject.toml`, `uv.lock` | Python/依赖与可执行入口 |

## 入口证据示例

- 项目/提交：TrendRadar / 固定提交
- 源码：`pyproject.toml`; `trendradar/__main__.py:1639-1696`
- 配置/函数：`[project.scripts]`, `main`
- 结论：主程序既能模块运行也能安装成命令；Python 要求是 `>=3.12`。
- 状态：`SOURCE_VERIFIED`

- 项目/提交：TrendRadar / 固定提交
- 源码：`mcp_server/server.py:1117-1257`
- 函数：`run_server`
- 结论：MCP 支持 stdio 和 HTTP，HTTP 默认全接口监听 3333 `/mcp`。
- 状态：`SOURCE_VERIFIED`

