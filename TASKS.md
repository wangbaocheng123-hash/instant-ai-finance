# 任务账本

状态只使用 `NOT_STARTED`、`IN_PROGRESS`、`BLOCKED`、`DONE`。任务完成必须有证据，不以“创建了空文件”代替研究成果。

| ID | 状态 | 任务 | 验收证据 |
|---|---|---|---|
| G0-001 | DONE | 保存用户 R0 原始总指令 | `docs/FOUNDATION_DIRECTIVE_R0.md` |
| G0-002 | DONE | 建立永久指令和三层记忆协议 | `AGENTS.md`、`docs/MEMORY_PROTOCOL.md` |
| G0-003 | DONE | 固化名称、Windows 桌面形态和 H 盘文件库 | `PROJECT_CHARTER.md`、`config/storage.paths.example.toml` |
| G0-004 | DONE | 建立状态、路线图、变更和上游锁定表 | `STATUS.md`、`ROADMAP.md`、`CHANGELOG.md`、`UPSTREAM_LOCK.yaml` |
| G0-005 | DONE | 建立记忆与泄漏检查脚本 | `scripts/check-project-memory.ps1` |
| G0-006 | DONE | 固定单机单用户、无账户体系并清理多用户规划 | ADR-0003、章程、永久指令与 MVP 蓝图 |
| R0-ENV-01 | DONE | 只读环境盘点，不安装工具 | `docs/ENVIRONMENT_REPORT.md` 与 `STATUS.md` |
| R0-CLONE-01 | DONE | 轻量克隆六个官方仓库并核验锁定提交 | 六仓均为官方 SSH 远程、浅仓且各含一个提交；详见 `docs/CLONE_LOG.md` |
| R0-STATIC-01 | DONE | 按统一模板完成六仓静态代码考古 | 六仓各 14 份规定报告均已通过最小完整性检查 |
| R0-RUN-01 | DONE | 按已授权范围进行受控运行验证 | TrendRadar 已完成隔离启动尝试；六仓 `TEST_RESULTS.md` 均记录结果或依赖审批阻塞 |
| R0-SYNTH-01 | DONE | 完成功能/许可证/架构对比与复用选型 | 六份总报告、九类模式库、ADR-0002 提案 |
| R0-CHECKPOINT-01 | DONE | R0 完成后创建本地 Git 检查点 | 本地提交 `R0-open-source-reconnaissance`，未推送远程 |
| D0-APPROVAL-01 | BLOCKED | 用户审阅并批准实施方案 | 用户决定与 ADR |
| P0-BOOTSTRAP-01 | BLOCKED | 建立正式产品工程骨架 | 仅在 D0 通过后开放 |

## 当前依赖说明

- 所有 `R0-*` 任务依赖用户授权开始 R0。
- `R0-STATIC-01` 依赖对应仓库完成锁定，但遇到运行依赖阻塞时仍继续静态分析。
- `R0-CLONE-01` 已通过用户手工登记的专用 SSH 公钥完成；该密钥只用于上游源码获取，不属于产品账户层。
- 更深入运行验证依赖用户批准各项目局部依赖安装；当前最低停止条件中的 TrendRadar 运行尝试已满足。
- 所有 `P*` 任务依赖 `D0-APPROVAL-01`，不得提前执行。
