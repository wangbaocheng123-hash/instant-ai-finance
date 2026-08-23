# OpenBB 模块地图

研究锚点：`OpenBB-finance/OpenBB`，提交 `3e071fcc2cd9f891cac6040ae60296dba76dab46`，`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。许可证列按根仓库 AGPL-3.0 保守处理；`desktop/LICENSE` 冲突另见 `LICENSE_NOTES.md`。

| 模块 | 作用与入口 | 主要依赖 | 独立性 | 复用建议/难度 | 许可证影响 |
|---|---|---|---|---|---|
| `openbb_platform/core/openbb_core/app` | Router、Query、CommandRunner、设置和服务 | FastAPI、Pydantic | 中；依赖 provider core | `DESIGN_REFERENCE`，中 | AGPL；直接复制/修改风险高 |
| `openbb_platform/core/openbb_core/provider` | Provider/Fetcher 抽象、Registry、180 个标准模型 | Pydantic、pandas、HTTP helpers | 高度模块化但与 OpenBB 类型绑定 | 优先通过 OpenBB 包/API 使用，低到中 | AGPL + provider 数据条款 |
| `openbb_platform/core/openbb` | 生成后的 Python `obb` 入口 | PackageBuilder、已安装 extensions | 低；依赖运行时生成 | `LIBRARY_DEPENDENCY` 候选，低 | 分发/修改需 AGPL 评估 |
| `openbb_platform/extensions/*` | equity/economy/commodity/news 等业务命令路由 | core 标准模型和 Router | 中；按包安装 | 只装所需 extension，中 | 每包 manifest 多为 AGPL |
| `openbb_platform/providers/*` | 32 个具体数据连接器 | 各供应商 SDK/API | 单 provider 可独立安装 | `ADAPTER`/`API_INTEGRATION`，低到中 | AGPL + API 条款双重约束 |
| `openbb_platform/extensions/platform_api` | `openbb-api`、widgets/apps/agents | core FastAPI、Uvicorn | 可作为独立本地服务 | `SIDE_CAR_SERVICE`，低 | AGPL 网络服务条款需评估 |
| `openbb_platform/extensions/mcp_server` | `openbb-mcp`，OpenAPI → MCP | FastMCP、core API | 可独立进程 | `SIDE_CAR_SERVICE` 或模式参考，中 | AGPL；外部智能体权限风险 |
| `openbb_platform/obbject_extensions/charting` | OBBject 图表 accessor | Plotly/pywry 等 | 可选扩展 | `DESIGN_REFERENCE`，中 | AGPL |
| `cli/openbb_cli` | 交互 CLI、routine scripts | openbb[all]、prompt-toolkit、rich | 消费完整平台 | 不纳入产品主链；模式参考，中高 | AGPL |
| `desktop/src` | React 环境管理 UI | React、Tauri plugins、OpenBB UI Pro | 与 Rust handlers 耦合 | 仅 UI/运维模式参考，高 | manifest AGPL；另有 MIT 文件冲突 |
| `desktop/src-tauri` | 环境安装、进程管理、配置、更新 | Rust 1.90、Tauri、Miniforge | 完整桌面管理器 | 不 Fork；仅设计参考，高 | Cargo 和嵌套 LICENSE 均 AGPL |
| `cookiecutter` | 生成 core/provider/obbject 扩展模板 | Cookiecutter | 独立开发工具 | `DESIGN_REFERENCE`，低 | 根许可证约束；生成物归属需复核模板声明 |
| `examples` | Python/Jupyter 使用示例 | 平台包 | 独立示例 | 仅参考，低 | 根许可证约束 |

## 核心依赖方向

```text
core/app/router
  -> ExtensionLoader
  -> ProviderInterface
  -> FastAPI

extension router
  -> core Query + OBBject

Query
  -> ProviderInterface
  -> QueryExecutor
  -> Registry -> Provider -> Fetcher

platform_api / mcp_server / CLI
  -> 同一 Router/CommandMap/OBBject

desktop
  -> 通过进程和配置文件管理 platform_api / mcp_server / Jupyter
```

## 可独立程度判断

- **最适合独立接入：** `openbb-api` 或精简安装的 Python `openbb` + 所需 provider/extension。
- **可以独立但不是第一优先：** `openbb-mcp`，适合未来让智能体按权限调用结构化金融工具。
- **不适合直接拆源码：** `ProviderInterface`/`PackageBuilder`/动态 Router 组合，内部耦合和 AGPL 风险都较高。
- **不适合作为“即时 AI”桌面壳：** `desktop` 重点是环境和后端管理，不是新闻阅读、证据浏览和情报处置 UI。

## 证据索引

- Provider 抽象：`openbb_platform/core/openbb_core/provider/abstract/provider.py::Provider`
- Fetcher 协议：`.../provider/abstract/fetcher.py::Fetcher`
- 扩展加载：`.../app/extension_loader.py::ExtensionLoader`
- 路由生成：`.../app/router.py::Router.command`
- API 包装：`.../api/router/commands.py::build_api_wrapper`
- MCP 设置：`.../extensions/mcp_server/openbb_mcp_server/models/settings.py::MCPSettings`
- 桌面安装：`desktop/src-tauri/src/tauri_handlers/startup.rs`

验证状态均为 `SOURCE_VERIFIED`；复用结论尚未经过运行和法律验证。
