# TrendRadar 测试结果

## 主任务受控运行验证（2026-08-23）

状态：`RUNTIME_VERIFIED_WITH_DEFECTS`。主任务从固定提交归档创建隔离副本 `experiments/TrendRadar-lab/source`，只在 `experiments/TrendRadar-lab/.venv` 安装依赖，未修改 `upstream/TrendRadar-snapshot`，未执行会自动安装 `uv` 的上游 bat 脚本。

### 依赖与环境

- Python 3.14.2；`pip install .` 成功；`pip check` 无损坏依赖。
- 虚拟环境共 101 个已安装分发包，占用 248.88 MiB。
- 普通 Windows GBK 控制台运行 `--doctor` 因 ✅/❌ emoji 触发 `UnicodeEncodeError`；改用 Python `-X utf8` 后通过。
- UTF-8 doctor：8 项通过、2 项警告、0 失败；警告为主动关闭 AI 和未配置通知渠道。

### 最小安全配置

- AI 分析/翻译、通知、远程存储全部关闭。
- 本地 SQLite/HTML 只写隔离实验目录。
- 第一次仅启用公开 Hacker News RSS；随后启用单一 `cls-hot` 财联社热榜完成主链验证。
- 未提供模型、通知或 S3 密钥，未启动 MCP。

### 两轮运行结果

| 验证 | 首轮 | 次轮 | 状态 |
|---|---|---|---|
| `cls-hot` 热榜 | 新增 13 条 | 新增 0、更新 13 | `RUNTIME_VERIFIED` |
| Hacker News RSS | 保存 20 条 | 新增 0、更新 20 | `RUNTIME_VERIFIED` |
| 新闻 SQLite | 13 items、1 platform | 保持 13 items | `RUNTIME_VERIFIED` |
| RSS SQLite | 20 items、20 distinct URLs | 保持 20 items | `RUNTIME_VERIFIED` |
| 排名历史 | 两次完整热榜运行后 26 条 | — | `RUNTIME_VERIFIED` |
| HTML | 生成 `17-14.html` | 生成 `17-15.html` 并更新 latest | `RUNTIME_VERIFIED` |
| 通知/AI | 均未调用 | 均未调用 | `DISABLED_BY_TEST_CONFIG` |

运行产物共 6.89 MiB，其中快照自带 7 个旧 news DB；本轮新增当日 news/RSS DB、doctor report 和 3 个 HTML 文件。

### 运行发现的缺陷

1. Windows 默认 GBK 控制台无法输出 doctor emoji；必须使用 UTF-8 模式或修复输出降级。
2. `platforms.enabled=false` 会令整个程序提前退出，RSS 即使启用也不会抓取，说明热榜总开关错误地门控 RSS-only 模式。
3. 在平台列表为空的 RSS-only 试验中，RSS 首轮成功保存 20 条、次轮数据库报告“新增 0、更新 20”，但业务层仍两次报告“检测到 20 条新增”；随后因当天 news 热榜为空触发“数据一致性检查失败”。
4. 上述严重错误仍以进程退出码 0 结束，不利于计划任务/监控发现失败。

因此可以确认依赖安装、配置加载、RSS/热榜抓取、SQLite 幂等保存、排名历史和 HTML 生成真实可运行；不能把 RSS-only、错误退出码或 Windows 默认控制台兼容性标为通过。

## 本子任务结论

**静态分析子任务本身未尝试运行。** 上述验证由主任务随后在独立实验目录完成；未修改 `upstream/TrendRadar-snapshot`，访问了 NewsNow 兼容 API、上游版本地址和公开测试 RSS，未访问 Jina/AI、未发送通知。

项目：TrendRadar；锁定提交：`8ee26026ba6c11dec41a95fb3895a7162876caa1`；来源：`OFFICIAL_ARCHIVE_SNAPSHOT`。

| 验证项 | 结果 | 状态 |
|---|---|---|
| 快照存在 | `upstream/TrendRadar-snapshot` 存在 | `SOURCE_VERIFIED` |
| `.git` | 不存在，因此不是完成克隆 | `SOURCE_VERIFIED` |
| 文件数/大小 | 162 文件；16,014,558 字节 | `SOURCE_VERIFIED` |
| 许可证文件 | 根 `LICENSE` 为 GPLv3 正文 | `SOURCE_VERIFIED` |
| 版本/入口 | 6.10.0；Python >=3.12；两个 console script | `SOURCE_VERIFIED` |
| 主程序启动 | 成功完成两轮受控主链运行 | `RUNTIME_VERIFIED` |
| MCP stdio/HTTP | 未尝试 | `NOT_ATTEMPTED` |
| 热榜抓取 | `cls-hot` 13 条；第二轮 0 新增/13 更新 | `RUNTIME_VERIFIED` |
| RSS 抓取 | 公开测试 Feed 20 条；第二轮 0 新增/20 更新 | `RUNTIME_VERIFIED_WITH_NEW_ITEM_DEFECT` |
| SQLite 保存 | news 13、RSS 20，distinct URL 20 | `RUNTIME_VERIFIED` |
| HTML 展示 | 两个时间快照和 latest 文件生成 | `RUNTIME_VERIFIED` |
| API/MCP 调用 | 未尝试 | `NOT_ATTEMPTED` |
| AI 分析/筛选/翻译 | 主动关闭，无真实密钥 | `NOT_ATTEMPTED_BY_DESIGN` |
| 通知渠道 | 主动关闭，无真实账号 | `NOT_ATTEMPTED_BY_DESIGN` |
| 依赖安装占用 | 101 包；`.venv` 248.88 MiB | `RUNTIME_VERIFIED` |
| 运行日志 | 主任务命令输出已核验并汇总于本文件 | `RUNTIME_VERIFIED` |

## 静态测试资产检查

快照文件清单未发现 `tests/`、`test_*.py` 或 pytest/unittest 配置；`pyproject.toml [dependency-groups].dev` 为空。因此不能把上游自动化测试覆盖率视为已知能力。状态：`SOURCE_VERIFIED`。

## 快照自带数据

`output/news/` 内含 7 个 2025-12-21 至 2025-12-27 的 `.db` 文件。这些不是本任务生成的运行结果，本任务也未查询其内容；不能作为当前提交在本机运行成功的证据。

## 后续验证

1. 修复并回归 RSS-only 门控、新增检测、错误退出码和 GBK 输出。
2. 单独验证 `collect=false` 是否仍采集。
3. MCP stdio 只读查询；HTTP 只绑 127.0.0.1。
4. AI、通知和远程存储需要真实账号/密钥时再单独批准；当前不影响 R0 底座判断。
