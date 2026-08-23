# OpenBB 扩展点

研究锚点：`OpenBB-finance/OpenBB`，提交 `3e071fcc2cd9f891cac6040ae60296dba76dab46`，`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

## 1. Core Router 扩展

- 声明：包的 `pyproject.toml` 注册组 `openbb_core_extension`。
- 加载：`core/openbb_core/app/extension_loader.py::ExtensionLoader.core_objects`。
- 接口：返回 OpenBB `Router`、FastAPI `APIRouter` 或 `FastAPI`；`RouterLoader.from_extensions()` 合并。
- 示例：`extensions/news/pyproject.toml` → `openbb_news.news_router:router`。

适合新增业务命令或把已有 FastAPI app 接入统一命令图。验证：`SOURCE_VERIFIED`。

## 2. Provider/Adapter 扩展

- 声明：entry-point 组 `openbb_provider_extension`。
- 对象：`Provider(name, credentials, fetcher_dict, ...)`。
- 每个 Fetcher 实现：`transform_query`、`extract_data/aextract_data`、`transform_data`。
- 标准模型：继承 `QueryParams` 和 `Data`；ProviderInterface 动态合并标准/额外字段。
- 示例：`providers/yfinance/openbb_yfinance/__init__.py::yfinance_provider`。

这是 OpenBB 最值得借鉴/接入的扩展点。验证：`SOURCE_VERIFIED`。

## 3. OBBject/输出扩展

- 声明：entry-point 组 `openbb_obbject_extension`。
- `ExtensionLoader._register_command_output_callbacks()` 可为全部或指定 route 注册输出回调。
- `StaticCommandRunner._trigger_command_output_callbacks()` 在结果返回前执行回调。
- `Env` 默认关闭 mutable extension 和 on-command-output，需要显式开启。
- Charting 是该机制的实际扩展示例。

适合输出转换或展示，不建议承担核心持久化，除非有严格幂等和错误隔离。验证：`SOURCE_VERIFIED`。

## 4. 认证扩展

- `AuthService` 通过 `OPENBB_API_AUTH_EXTENSION` 指定 core entry point。
- 扩展模块需要提供 `router`、`auth_hook`、`user_settings_hook`。

适合替换默认 Basic Auth。验证：`SOURCE_VERIFIED`。

## 5. REST/OpenAPI

- 核心 FastAPI app：`core/openbb_core/api/rest_api.py::app`。
- `Router.command()` 自动把命令变成 GET/POST route 并生成 schema。
- `platform_api` 增加 Workspace widgets/apps/agents endpoints。
- coverage routes 可查询 provider 与 command 映射。

推荐“即时 AI”通过本地 REST sidecar 接入，避免直接耦合 OpenBB 内部生成包。验证：`SOURCE_VERIFIED`。

## 6. MCP

- `mcp_server/app/app.py` 由 FastAPI/OpenAPI 路由生成 FastMCP tools/resources/prompts。
- route 可在 `openapi_extra.mcp_config` 中控制 expose、MCP 类型、HTTP 方法、排除参数和关联 prompts。
- MCPSettings 可限制类别并配置 tool discovery、skills、认证和缓存。

适合未来智能体按需查询金融数据；不是 AI 摘要引擎。验证：`SOURCE_VERIFIED`。

## 7. CLI 和 routine

- CLI 根据 `obb.reference` 动态生成命令菜单。
- `.openbb` routines 由 `script_parser.py::parse_openbb_script` 处理变量、循环和命令序列，再由 `cli_controller.py::run_scripts` 执行。
- 可作为外部 Windows Task Scheduler 的命令入口，但仓库没有内建通用定时器。

验证：`SOURCE_VERIFIED`；外部调度只为推论，`UNVERIFIED`。

## 8. Cookiecutter

- `cookiecutter/openbb_cookiecutter/template/.../pyproject.toml` 可生成 core/provider/obbject 扩展骨架。
- 模板展示三类 entry points 的官方结构。

验证：`SOURCE_VERIFIED`。

## 目标指令要求的其他扩展点

| 扩展点 | 结论 |
|---|---|
| 插件/Provider/Adapter | 强，源码已验证 |
| API | 强，FastAPI/OpenAPI |
| MCP | 强，可选扩展 |
| CLI | 强，交互 + routines |
| 数据库接口 | 无统一业务 DB adapter；只有 provider 局部缓存 |
| RSS | 不是通用 RSS 平台；SEC provider 有 `RssLitigation` 特定模型 |
| Webhook | 未发现通用 webhook 接收/发送框架 |
| 自定义节点 | 没有 n8n 式节点系统；相近概念是 Router/Provider 扩展 |
| 自定义数据源 | 强，通过 Provider + Fetcher |
| 调度 | 无内建通用调度器 |

“未发现”均基于固定快照的 core/extensions/CLI 文件枚举和关键字/调用链检查，为 `SOURCE_VERIFIED` 的仓内结论。
