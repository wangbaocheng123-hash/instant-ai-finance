# OpenBB 配置

研究锚点：`OpenBB-finance/OpenBB`，提交 `3e071fcc2cd9f891cac6040ae60296dba76dab46`，`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

## 配置优先级与位置

### 核心设置

- `openbb_platform/core/openbb_core/app/constants.py`
  - `OPENBB_DIRECTORY = ~/.openbb_platform`
  - `USER_SETTINGS_PATH = ~/.openbb_platform/user_settings.json`
  - `SYSTEM_SETTINGS_PATH = ~/.openbb_platform/system_settings.json`
- `SystemSettings.create_openbb_directory()` 在初始化时创建目录和默认 JSON。
- `UserSettings` 包含 `credentials`、`preferences`、`defaults`。

状态：`SOURCE_VERIFIED`。

### 环境变量

`openbb_platform/core/openbb_core/env.py::Env` 先加载 `~/.openbb_platform/.env`，再读取进程环境：

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `OPENBB_API_AUTH` | 是否启用核心 API 认证 | false |
| `OPENBB_API_USERNAME` / `OPENBB_API_PASSWORD` | HTTP Basic 凭据 | 空 |
| `OPENBB_API_AUTH_EXTENSION` | 自定义认证扩展 | 空 |
| `OPENBB_AUTO_BUILD` | import 时自动重建扩展 package | true |
| `OPENBB_DEBUG_MODE` | 调试异常 | false |
| `OPENBB_DEV_MODE` | 暴露开发路由 | false |
| `OPENBB_ALLOW_MUTABLE_EXTENSIONS` | 允许可变 OBBject 扩展 | false |
| `OPENBB_ALLOW_ON_COMMAND_OUTPUT` | 允许命令输出回调 | false |

状态：`SOURCE_VERIFIED`。

### 凭据

- `CredentialsLoader.load()` 从 provider entry points 汇总所需 credential 名称。
- `user_settings.json` 中的 credentials 与环境变量合并；环境键若匹配已知 credential 或以 `API_KEY` 结尾，会转为 Pydantic `SecretStr`。
- `UserService.write_to_file()` 以 JSON 写入设置；`desktop/.../credentials.rs::update_user_credentials_impl` 也会把值序列化进同一个 JSON。

结论：内存显示会掩码，但文件不是密钥保险库。建议仅用受限权限的 `.env`/进程环境或未来产品自己的安全凭据存储，绝不能提交到 Git。状态：`SOURCE_VERIFIED`。

## Preferences

`core/openbb_core/app/model/preferences.py::Preferences`：

- `cache_directory`
- `data_directory`
- `export_directory`
- `user_styles_directory`
- `request_timeout`，默认 60 秒
- `metadata`、`show_warnings`
- `output_type`：`OBBject/dataframe/polars/numpy/dict/chart/llm`

集成时需把数据、缓存和导出路径映射到 `H:\即时AI文件库`，并隔离在 `openbb` 子目录。状态：`SOURCE_VERIFIED`，H 盘运行未验证。

## API 配置

- `core/openbb_core/app/model/api_settings.py::APISettings`
  - 默认前缀 `/api/v1`。
  - CORS 默认 `allow_origins/methods/headers = ["*"]`。
- `PythonSettings.http`
  - 支持 CA、客户端证书、代理、timeout、headers、cookies 等，传给 requests/aiohttp helpers。
- `PythonSettings.uvicorn`
  - 传给 `python -m openbb_core.api.rest_api` 和 `openbb-api`。
- `platform_api/main.py::launch_api`
  - host 默认 `OPENBB_API_HOST` 或 `127.0.0.1`；port 默认 `OPENBB_API_PORT` 或 `6900`。

状态：`SOURCE_VERIFIED`。

## MCP 配置

`MCPService` 的优先级：CLI overrides → 环境变量 → `~/.openbb_platform/mcp_settings.json` → 默认值。

`MCPSettings` 关键项：

- `OPENBB_MCP_DEFAULT_TOOL_CATEGORIES` / `ALLOWED_TOOL_CATEGORIES`
- `OPENBB_MCP_ENABLE_TOOL_DISCOVERY`
- `OPENBB_MCP_SYSTEM_PROMPT_FILE` / `SERVER_PROMPTS_FILE`
- `OPENBB_MCP_DEFAULT_SKILLS_DIR` / `SKILLS_PROVIDERS`
- `OPENBB_MCP_CACHE_EXPIRATION_SECONDS`
- `OPENBB_MCP_MASK_ERROR_DETAILS`
- `OPENBB_MCP_UVICORN_CONFIG`，默认 `127.0.0.1:8001`
- `OPENBB_MCP_CLIENT_AUTH` / `SERVER_AUTH`

状态：`SOURCE_VERIFIED`。

## Desktop 配置

- `desktop/src-tauri/tauri.conf.json`：产品 ID、窗口、updater、Vite dev URL。
- `desktop/src-tauri/tauri.windows.conf.json`：NSIS、current-user、WebView bootstrapper。
- `desktop/src-tauri/src/tauri_handlers/startup.rs`：生成 Conda YAML，并安装 `openbb-platform-api`、`openbb-mcp-server`、Jupyter 等。

这套桌面首次安装会下载/安装大型依赖，未经用户批准不得尝试。状态：`SOURCE_VERIFIED`。

## 安全基线建议（仅研究结论）

1. API/MCP 只绑定 `127.0.0.1`。
2. 若非单用户可信环境，显式启用认证；不要沿用 auth=false。
3. 收紧 CORS；不要保留 `*`。
4. MCP 只允许白名单类别，默认关闭工具热发现和外部 skills providers。
5. 禁止把 provider keys 放入仓库、日志或导出；对 `user_settings.json` 做文件权限加固。
6. 把 cache/export 指向 H 盘，但凭据仍应与业务数据库分离。

以上建议未实施，`UNVERIFIED`。
