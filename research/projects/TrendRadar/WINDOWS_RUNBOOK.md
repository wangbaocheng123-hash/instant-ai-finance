# TrendRadar Windows 运行手册（静态推导，未执行）

项目/提交：TrendRadar / `8ee26026ba6c11dec41a95fb3895a7162876caa1`。来源为 `OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`，不算完成克隆。本文件记录源码提供的 Windows 路径，不代表已成功运行。

## 能否直接运行

源码显示可原生 Windows 运行，不强制 WSL 或 Docker：`pyproject.toml` 是纯 Python 包，根目录提供 `setup-windows.bat` 与 `start-http.bat`。但运行状态为 `NOT_ATTEMPTED`。

### 先决条件

- Python **3.12+**：以 `pyproject.toml requires-python = ">=3.12"` 为准。
- uv：Windows 脚本用 uv 创建 `.venv` 并同步锁文件。
- 网络：首次依赖解析、NewsNow/RSS、版本检查、AI、通知会访问外部网络。
- Docker/WSL：原生路径不需要；容器路径是可选部署方式。

注意：`setup-windows.bat` 提示“Python 3.10+”，与 pyproject 3.12+ 冲突，应视为上游脚本文案缺陷。

## 安全的建议验证顺序

以下命令**均未由本子任务执行**。由于上游 bat 会在缺 uv 时执行远程 PowerShell 安装脚本或 pip 安装，未经用户批准不要双击它。

```powershell
# 1. 只读确认（由主任务环境检查决定是否执行）
python --version
uv --version

# 2. 获得依赖安装批准后，在快照或实验副本中同步
uv sync --frozen --no-dev

# 3. 先做诊断
uv run python -m trendradar --doctor

# 4. 检查调度解析
uv run python -m trendradar --show-schedule

# 5. 获得联网抓取批准后运行一次
uv run python -m trendradar
```

上游脚本实际使用 `uv sync`，GitHub workflow 使用 `uv sync --frozen --no-dev`；建议验证采用 frozen，以避免锁文件漂移。

## MCP

### stdio（推荐先测）

```powershell
uv run python -m mcp_server.server --transport stdio
```

### HTTP（只绑定本机）

上游 `start-http.bat` 固定绑定 `0.0.0.0:3333`，不建议直接使用。安全验证应改用命令行参数：

```powershell
uv run python -m mcp_server.server --transport http --host 127.0.0.1 --port 3333
```

端点：`http://127.0.0.1:3333/mcp`。按 `Ctrl+C` 停止。

## 主程序与输出

- CLI 批处理本身没有监听端口。
- HTML 是本地静态文件，主程序在非 Docker/GitHub Actions 环境可能用 `webbrowser.open(file://...)` 打开。
- Docker 静态 Web 默认映射到宿主 `127.0.0.1:8080`。
- MCP HTTP 默认 3333。
- 默认输出目录为仓库内 `output`。若未来实验接入“即时 AI”，必须把 `storage.local.data_dir` 指向 `H:\即时AI文件库` 下的实验隔离目录，不能污染 Git；本研究未改配置。

## 常见错误（源码可预见）

| 错误 | 原因/处理 |
|---|---|
| Python 版本不兼容 | 使用 3.12+，不要按 bat 的 3.10+ 文案 |
| `uv` 不存在 | 等待用户批准安装；不要让 bat 自动执行 `irm ... | iex` |
| 配置文件缺失 | 确保 `config/config.yaml`、`frequency_words.txt`、timeline 文件存在 |
| AI key 缺失 | keyword 模式仍可用；AI 功能会返回配置错误 |
| MCP `.venv` 未找到 | `start-http.bat` 会退出；先按批准流程 sync |
| API/RSS 超时 | 检查代理、API 地址、feed 可用性；不要禁用 TLS/域名校验 |
| 3333 被占用 | 改 `--port`；优先 127.0.0.1 |
| SQLite 写入失败 | 检查 `storage.local.data_dir` 权限与 H 盘可用性 |

## 停止与清理

- 前台 CLI/MCP：`Ctrl+C`。
- 依赖位于 `.venv`；未经用户明确授权，不做删除。
- 数据清理由 `retention_days` 和 `StorageManager.cleanup_old_data` 控制；首次验证前建议保留并人工核对。
- Docker 的停止/删除命令本子任务不提供执行结果，因未部署。

## 待运行采集的证据

需要保存：Python/uv 版本、`uv sync` 下载与磁盘量、doctor 输出、首次抓取日志、生成的两类 DB/HTML、MCP stdio 查询、端口监听地址、停止后的残留进程。当前全部 `UNVERIFIED`。
