# OpenBB Windows 运行手册（静态研究版）

研究锚点：`OpenBB-finance/OpenBB`，提交 `3e071fcc2cd9f891cac6040ae60296dba76dab46`，`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

## 当前结论

- Python/REST/CLI/MCP 路径理论上可以直接运行在 Windows，不要求 WSL 或 Docker。
- 仓库还提供 Windows Tauri 桌面环境管理器和 NSIS 配置，但它会安装 Miniforge、Node/Jupyter 和 OpenBB 服务依赖，属于大型安装路径。
- 本轮**没有安装依赖、没有启动任何进程、没有写用户配置**。以下命令是源码/仓内文档归纳，不是运行通过证明。

验证状态：`SOURCE_VERIFIED` + `RUNTIME_UNVERIFIED`。

## 路径 A：最小 Python 数据接口（建议优先验证）

### 要求

- `openbb_platform/core/pyproject.toml`：Python `>=3.10,<4`。
- 聚合包 `openbb_platform/pyproject.toml`：OpenBB `4.7.3`，默认带多项 provider/extension。
- 从源码开发还需 Poetry；`openbb_platform/README.md` 给出 `python dev_install.py -e`。

### 上游安装/调用命令

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install openbb
python -c "from openbb import obb; print(obb.system)"
```

这些命令**未执行**。正式实验应放入 `experiments/OpenBB-lab/`，不要修改 `upstream/OpenBB-snapshot`。

### 最小数据调用候选

```python
from openbb import obb
result = obb.equity.price.historical("AAPL", provider="yfinance")
print(result.to_dataframe().tail())
```

上游 README 只有示例，真实网络调用本轮未验证。建议实际运行时优先选择无 key provider，再验证 SEC/FRED/FED/CFTC/EIA；不得把真实 key 写进仓库。

## 路径 B：本地 REST sidecar（推荐产品集成形态候选）

### 入口

- `openbb-api`：`openbb_platform/extensions/platform_api/pyproject.toml`。
- 默认 host/port：`127.0.0.1:6900`，见 `platform_api/main.py::launch_api`。

```powershell
openbb-api --host 127.0.0.1 --port 6900
```

检查候选：

```powershell
Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:6900/'
Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:6900/docs'
```

停止：在启动终端按 `Ctrl+C`。若作为产品 sidecar，应由桌面进程管理器保存 PID 并做优雅终止，不能用模糊进程名批量杀进程。

状态：命令来自源码/README，`RUNTIME_UNVERIFIED`。

## 路径 C：MCP

- 安装包：`openbb-mcp-server`。
- 入口：`openbb-mcp`。
- 默认 HTTP：`127.0.0.1:8001`；桌面预设命令明确为 streamable-http。

```powershell
openbb-mcp --transport streamable-http --host 127.0.0.1 --port 8001
```

停止：`Ctrl+C`。正式验证前应在 `mcp_settings.json` 限定 `allowed_tool_categories`，设置认证并保持 localhost。状态：`RUNTIME_UNVERIFIED`。

## 路径 D：CLI

- 安装：`openbb-cli`。
- 入口：`openbb`。
- `cli/pyproject.toml` 会依赖 `openbb[all]`、charting、prompt-toolkit、rich 等，依赖面比最小 Python/API 更大。

```powershell
python -m pip install openbb-cli
openbb
```

`.openbb` routine 可重放命令，但没有内建调度器。停止：CLI 内退出命令或 `Ctrl+C`。状态：`RUNTIME_UNVERIFIED`。

## 路径 E：桌面源码

### 源码声明的要求

- Rust `1.90.0`：`desktop/src-tauri/Cargo.toml::rust-version`。
- Node.js/npm：`desktop/package.json`。
- OpenSSL 环境：`desktop/README.md`。
- 开发命令：

```powershell
Set-Location desktop
npm install
npm run tauri dev
```

首次运行逻辑 `desktop/src-tauri/src/tauri_handlers/startup.rs` 会：

1. 查询 GitHub Miniforge releases；
2. 下载 Windows `.exe` installer；
3. 安装 Miniforge；
4. 生成 `~/.openbb_platform/environments/openbb.yaml`；
5. 安装 Jupyter、`openbb-platform-api`、`openbb-mcp-server` 等；
6. 创建 6900 API 和 8001 MCP 默认后端。

因此它不是本轮可直接尝试的轻量验证。无需 WSL/Docker，但需要用户批准 Rust/Node/OpenSSL/Miniforge 和大量 Python/Node 依赖。状态：`SOURCE_VERIFIED`。

## 端口和文件

| 项目 | 默认值 | 来源 |
|---|---:|---|
| Workspace API | 6900 | `platform_api/main.py`、desktop startup |
| MCP | 8001 | `MCPSettings`、desktop startup |
| Vite desktop dev | 1470 | `desktop/package.json`、Tauri config |
| Core REST README 示例 | 8000 | `openbb_platform/README.md`；不是 `openbb-api` 默认 |
| 配置 | `%USERPROFILE%\.openbb_platform` | `app/constants.py` |
| 数据 | `%USERPROFILE%\OpenBBUserData` | `Preferences` |

## H 盘实验前置

运行前应在临时/实验设置中把 `data_directory`、`cache_directory`、`export_directory` 指向 `H:\即时AI文件库` 下的 OpenBB 子目录，并确认日志不会写研究仓库。真实凭据不进入 Git。

## 常见风险与预计成本

- provider key 缺失：`QueryExecutor.filter_credentials()` 会抛错。
- import 自动 build：`OPENBB_AUTO_BUILD` 默认 true，可能生成 package/reference 文件。
- API 暴露：auth 默认关闭，CORS 默认 `*`，不可绑定公网接口。
- 桌面源码构建：Rust/npm/OpenSSL/Tauri 依赖大；官方 README 只说成品约 35 MB、压缩 12 MB，**未给首次环境总占用**。该大小为 `DOC_ONLY`：仅在文档中发现，尚未通过源码产物或本轮运行验证。
- Miniforge + Jupyter + OpenBB 全量依赖预计显著大于桌面壳本身；源码无准确数值，必须先做下载/磁盘预算再申请批准，不能把估算伪装成实测。

## 清理原则

本轮未产生依赖和运行数据，无需清理。未来实验停止后只删除明确创建的 `experiments/OpenBB-lab` 虚拟环境与 H 盘 OpenBB 测试子目录；不得递归删除用户目录、`H:\即时AI文件库` 根或 `~/.openbb_platform`，除非已逐项确认并获得授权。
