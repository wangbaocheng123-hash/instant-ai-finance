# OpenBB 入口清单

研究锚点：OpenBB，提交 `3e071fcc2cd9f891cac6040ae60296dba76dab46`，`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

## 主入口

| 类型 | 源码/配置 | 入口 | 作用 | 状态 |
|---|---|---|---|---|
| Python SDK | `openbb_platform/core/openbb/__init__.py` | `obb` / `sdk` | 自动构建扩展模块并创建 `BaseApp` | `SOURCE_VERIFIED` |
| Build CLI | `openbb_platform/core/pyproject.toml` | `openbb-build = openbb_core.build:main` | 重建扩展生成包和 reference | `SOURCE_VERIFIED` |
| 交互 CLI | `cli/pyproject.toml` | `openbb = openbb_cli.cli:main` | bootstrap 后启动交互控制器 | `SOURCE_VERIFIED` |
| 核心 REST | `openbb_platform/core/openbb_core/api/rest_api.py` | `app` / `python -m ...rest_api` | FastAPI app；模块入口调用 Uvicorn | `SOURCE_VERIFIED` |
| Workspace API | `openbb_platform/extensions/platform_api/pyproject.toml` | `openbb-api = openbb_platform_api.main:main` | REST + widgets/apps/agents，默认 6900 | `SOURCE_VERIFIED` |
| MCP | `openbb_platform/extensions/mcp_server/pyproject.toml` | `openbb-mcp = openbb_mcp_server.app.app:main` | 将 API/OpenAPI 路由暴露为 MCP | `SOURCE_VERIFIED` |
| Desktop 前端 | `desktop/src/main.tsx` | React `createRoot` | Tauri 窗口 UI | `SOURCE_VERIFIED` |
| Desktop 后端 | `desktop/src-tauri/src/main.rs` | Rust `main` | Tauri commands、托盘、更新、后端管理 | `SOURCE_VERIFIED` |

## API 路由入口

- `openbb_platform/core/openbb_core/api/rest_api.py::app`
  - `AppLoader.add_routers()` 加载 command、coverage；DEV_MODE 下另加 auth/system。
  - API 前缀由 `APISettings.prefix` 生成，默认 `/api/v1`。
- `openbb_platform/core/openbb_core/api/router/commands.py::add_command_map`
  - 从 `RouterLoader.from_extensions()` 获得所有业务 routes，并包装成 `CommandRunner` 调用。
- `openbb_platform/core/openbb_core/api/router/coverage.py`
  - `/coverage/providers`、`/coverage/commands`、`/coverage/command_model`。
- `openbb_platform/extensions/platform_api/openbb_platform_api/main.py`
  - `/`、`/widgets.json`、`/apps.json`、可选 `/agents.json`。

验证状态：`SOURCE_VERIFIED`。

## Extension/Provider 入口

每个包通过 `pyproject.toml` 注册 Python entry point。例如：

- `openbb_platform/extensions/news/pyproject.toml`
  - `news = "openbb_news.news_router:router"`，组 `openbb_core_extension`。
- `openbb_platform/extensions/commodity/pyproject.toml`
  - `commodity = "openbb_commodity.commodity_router:router"`。
- `openbb_platform/providers/yfinance/pyproject.toml`
  - `yfinance = "openbb_yfinance:yfinance_provider"`，组 `openbb_provider_extension`。
- `openbb_platform/providers/sec/pyproject.toml`
  - `sec = "openbb_sec:sec_provider"`。
- `openbb_platform/obbject_extensions/charting/pyproject.toml`
  - `openbb_charting = "openbb_charting:ext"`，组 `openbb_obbject_extension`。

加载入口：`openbb_platform/core/openbb_core/app/extension_loader.py::ExtensionLoader`。验证：`SOURCE_VERIFIED`。

## CLI routine 入口

- `cli/openbb_cli/controllers/cli_controller.py::run_scripts`、`run_routine`
- `cli/openbb_cli/controllers/script_parser.py::parse_openbb_script`

它们读取和展开 `.openbb` 命令脚本，支持参数与循环；没有发现内建时钟或队列调度器。验证：`SOURCE_VERIFIED`。

## 桌面和打包入口

- 开发：`desktop/package.json`
  - `npm run dev` → Vite 1470。
  - `npm run tauri` → Tauri CLI。
  - `npm run build` → TypeScript + Vite。
- Tauri 配置：`desktop/src-tauri/tauri.conf.json`
  - `beforeDevCommand: npm run dev`，`devUrl: http://localhost:1470`。
- Windows 打包：`desktop/src-tauri/tauri.windows.conf.json`
  - NSIS，current-user 安装；打包前执行 `sign.ps1`。
- 默认后台服务：`desktop/src-tauri/src/tauri_handlers/startup.rs`
  - `openbb-api --host 127.0.0.1 --port 6900`
  - `openbb-mcp --transport streamable-http --host 127.0.0.1 --port 8001`

验证状态：`SOURCE_VERIFIED`，未运行。

## Docker 与定时任务

- 快照中未发现 Dockerfile 或 compose 文件，因此没有仓库内 Docker 主入口。
- 未发现通用定时任务服务入口。CLI routines 只能作为外部调度器调用的脚本入口。

验证状态：`SOURCE_VERIFIED`（基于快照文件枚举和 core/extensions/CLI 搜索）。

## 关键配置文件

| 路径 | 用途 |
|---|---|
| `openbb_platform/pyproject.toml` | 聚合包版本 `4.7.3`、默认/可选 providers 和 extensions |
| `openbb_platform/core/pyproject.toml` | 核心依赖，Python `>=3.10,<4` |
| `~/.openbb_platform/user_settings.json` | credentials、preferences、defaults；源码常量见 `app/constants.py` |
| `~/.openbb_platform/system_settings.json` | API、Python HTTP/Uvicorn、日志等系统设置 |
| `~/.openbb_platform/.env` | `OPENBB_*` 环境变量 |
| `~/.openbb_platform/mcp_settings.json` | MCP 配置 |
| `desktop/src-tauri/tauri.conf.json` | 桌面通用配置和 updater |
| `desktop/src-tauri/tauri.windows.conf.json` | Windows bundle 配置 |

所有 `~` 路径都是上游默认值；集成“即时 AI”时必须显式改到 `H:\即时AI文件库` 对应目录，不能采用默认用户目录。
