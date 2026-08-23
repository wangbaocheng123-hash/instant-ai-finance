# OpenBB 测试与运行结果

研究锚点：`OpenBB-finance/OpenBB`，提交 `3e071fcc2cd9f891cac6040ae60296dba76dab46`，`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

## 本轮状态

**运行未尝试。** 本子任务被明确限定为静态分析；没有安装 Python/npm/Rust/Poetry/Miniforge 依赖，没有启动 CLI、Python、REST、MCP 或桌面，也没有调用外部数据源。

| 检查项 | 结果 |
|---|---|
| 官方源码下载 | 已有官方固定提交归档快照 |
| Git 克隆 | 未完成；快照无 `.git` |
| 成功启动 | 未尝试 |
| 启动截图/日志 | 无 |
| 成功抓取数据 | 未尝试 |
| 成功保存数据 | 未尝试 |
| 成功展示 | 未尝试 |
| 成功调用 REST API | 未尝试 |
| 成功调用 MCP | 未尝试 |
| Windows 直接运行 | 源码支持，运行未验证 |
| Docker/WSL | 不属于 Python 主路径要求；未使用 |
| 新增依赖磁盘占用 | 0；未安装依赖 |
| 产生无关依赖 | 否 |

## 静态可测试性证据

- 发现 223 个测试文件：CLI 23、desktop 18、OpenBB platform 181、examples 1。
- provider tests 含 302 个记录的 HTTP YAML cassettes。
- `pytest.ini` 的 testpaths 覆盖 `tests` 和 `openbb_platform/**/tests`，定义 `linux` 与 `integration` markers。
- `desktop/vitest.config.ts` 使用 Vitest + jsdom。
- Rust 源码内含 `#[test]`/`#[tokio::test]`，例如 startup、command sanitizer、credentials 和 backends。

这只能证明仓库包含测试资产，不能证明该提交测试通过。状态：`SOURCE_VERIFIED`。

## 快照资源占用

- 快照文件：2,191。
- 解压后静态大小：239,399,794 字节（约 228.3 MiB）。
- `.git`：不存在。
- 依赖、虚拟环境、node_modules、Rust target、数据库和运行日志：本轮均未创建。

以上是本地只读枚举实测，`SOURCE_SNAPSHOT_VERIFIED`。

## 未运行原因与预计成本

1. 项目 R0 规定先做静态分析，再按优先级运行；OpenBB 属第二优先级。
2. 聚合 Python 包依赖多个 provider/extensions；CLI 使用 `openbb[all]`；桌面还需 Rust、Node、OpenSSL、Miniforge、Jupyter。
3. 多数 provider 需要网络或 API key，必须先选择最小验证集并规划密钥隔离。
4. 用户未在本子任务中批准任何大型安装。

官方桌面 README 只给成品壳约 35 MB/压缩 12 MB，未给 Miniforge/Jupyter/OpenBB 环境总大小。这是 `DOC_ONLY`：仅在文档中发现，尚未通过源码产物或本轮运行验证。不能从源码得出可靠下载量；运行前应单独估算并请求批准。

## 建议的后续受控测试矩阵

仅在主任务和用户批准后：

1. 在 `experiments/OpenBB-lab` 创建隔离 venv，不修改快照。
2. 只装核心 + 最少 extension/provider，先不装 CLI `[all]`、MCP、charting 和 desktop。
3. 设置 H 盘 cache/export 临时子目录，记录安装前后磁盘。
4. 先 import `obb` 并记录 auto-build 副作用。
5. 调用一个无需 key 的结构化行情/SEC endpoint，再调用 localhost REST。
6. 核验结果 schema、provider、URL、时间戳、错误处理和停止/清理。
7. MCP 和桌面分别作为后续独立批准项。

当前结论不能标记 `RUNTIME_VERIFIED`。
