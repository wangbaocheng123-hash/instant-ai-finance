# changedetection.io 测试与运行结果

## 本子任务结果

| 项目 | 结果 |
|---|---|
| 静态源码读取 | 完成 |
| 依赖安装 | **未尝试** |
| 项目启动 | **未尝试** |
| 抓取数据 | **未尝试** |
| 保存数据 | **未尝试** |
| Web UI 展示 | **未尝试** |
| REST API 调用 | **未尝试** |
| 通知发送 | **未尝试** |
| Docker/WSL | **未尝试；当前无批准** |

原因：本子任务被明确限定为静态分析，不安装依赖、不运行项目。不能把源码中存在测试、README 命令或 Dockerfile 当成运行成功。

运行状态：`NOT_ATTEMPTED_BY_THIS_SUBTASK`。

## 快照检查

- 路径：`upstream/changedetection-snapshot`
- 模式：`OFFICIAL_ARCHIVE_SNAPSHOT`
- 固定提交标识：`fce24780e74199bf34c62a0d90188cc2fc12f061`（由上游锁定记录提供）
- `.git`：不存在，**不能算正式 clone 完成**
- 文件数：1111
- 解压文件总大小：15,363,937 bytes，约 14.65 MiB
- 运行新增磁盘占用：0（本子任务未安装/未运行）
- 截图/日志：无，因为未启动

## 静态测试资产

快照中定位到 132 个 `test_*.py` 文件，覆盖 API/auth/security、scheduler/queue、filters/diff/history、RSS、backup、notifications、LLM、plugins、proxy、restock、Socket.IO 等，例如：

- `changedetectionio/tests/test_scheduler.py`
- `changedetectionio/tests/test_queue_handler.py`
- `changedetectionio/tests/test_commit_persistence.py`
- `changedetectionio/tests/test_history_consistency.py`
- `changedetectionio/tests/test_api_security.py`
- `changedetectionio/tests/unit/test_validate_url.py`
- `changedetectionio/tests/unit/test_processor_config_path_traversal.py`
- `changedetectionio/tests/test_notification.py`
- `changedetectionio/tests/llm/test_evaluator.py`
- `changedetectionio/tests/plugins/test_processor.py`

这些只证明存在测试源码，不证明该固定提交在当前 Windows 环境通过。验证状态：`SOURCE_VERIFIED_TEST_ASSETS_PRESENT`，执行结果 `UNVERIFIED`。

## 预计运行成本（静态估计）

- `requirements.txt` 依赖数量和体量较大，包含 cryptography、lxml、Selenium、Pyppeteer、OpenAPI、LiteLLM、测试依赖等；实际下载/venv 大小未测。
- Dockerfile 还安装 Playwright、可选 OpenCV 和多个 Debian 包；browser service 是额外镜像/内存开销。
- Windows 基础 HTTP 验证应先使用项目局部 venv，关闭 browser/LLM，仅 1 worker；需要用户批准后由主任务执行。

不得把静态估计写成实际下载量。

## 后续受控验收建议

1. `--version`/`--help`；
2. 空临时 datastore 启动并绑定 `127.0.0.1`；
3. UI 创建本地授权测试 URL watch；
4. 连续两次抓取，验证 `watch.json`、history、snapshot、diff；
5. API key 的 create/recheck/history/diff；
6. RSS token；
7. `null://` notification；
8. 进程正常停止和 datastore 恢复；
9. 记录 venv、datastore、日志、峰值内存和下载量；
10. browser/LLM/PDF 作为独立、另批准的扩展测试。

项目：`dgtlmoon/changedetection.io`；提交：`fce24780e74199bf34c62a0d90188cc2fc12f061`。

