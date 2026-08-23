# OpenBB 许可证笔记

研究锚点：`OpenBB-finance/OpenBB`，提交 `3e071fcc2cd9f891cac6040ae60296dba76dab46`，`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

> 本文件是技术尽调记录，不是法律意见。正式分发、网络服务、商业使用或源码组合前需由合格律师审阅。

## 根许可证

- `LICENSE:1-3`：Copyright (c) 2021-2025 OpenBB Inc.；声明仓库所有文件使用 GNU Affero General Public License v3.0。
- `openbb_platform/pyproject.toml`：`license = "AGPL-3.0-only"`。
- `openbb_platform/core/pyproject.toml`：`AGPL-3.0-only`。
- `cli/pyproject.toml`：`AGPL-3.0-only`。
- `desktop/package.json`：`AGPL-3.0`。
- `desktop/src-tauri/Cargo.toml`：`AGPL-3.0`。

保守结论：OpenBB 核心、CLI、provider/extensions 和桌面 manifests 均应按 AGPL-3.0 处理。状态：`SOURCE_VERIFIED`。

## AGPL 技术影响提示

根许可证包含：

- 第 5 节：修改源码版本的传播要求。
- 第 6 节：非源码形式传播及相应源码要求。
- 第 13 节：修改版本通过网络与用户交互时的对应源码提供要求。

这意味着完整 Fork、修改后提供本地/网络服务、把组件随 Windows 客户端分发，均可能触发强 copyleft 义务。调用一个独立、未修改的本地服务是否使调用方成为衍生作品，不能由本技术报告定论；`SIDE_CAR_SERVICE` 只是降低代码混合程度，不自动消除许可证义务。

## 桌面目录的冲突信号

- `desktop/LICENSE`：MIT License，Copyright (c) 2025 OpenBB。
- 同目录 `desktop/package.json`：`"license": "AGPL-3.0"`。
- 子目录 `desktop/src-tauri/LICENSE`：完整 AGPL 文本。
- `desktop/src-tauri/Cargo.toml`：AGPL-3.0。
- 根 `LICENSE` 又声明“all files in this repository”均为 AGPL。

冲突不能静默解释。没有额外范围文件证明 MIT 只覆盖某些前端文件，因此当前应以更保守的 AGPL 处理，并向上游/法律顾问确认 `desktop/LICENSE` 的准确范围。状态：`SOURCE_VERIFIED` 的冲突事实；适用范围 `UNVERIFIED`。

## 第三方依赖与数据条款

代码许可证不等于数据再分发权：

- provider 包连接 FMP、Benzinga、Intrinio、Tiingo、TradingEconomics 等付费/凭据来源，也连接 SEC、FRED、FED、BLS、CFTC、EIA 等公共来源。
- 每个 provider 的 API 服务条款、缓存期限、引用和再分发限制需要逐一核查。
- yfinance/Finviz/WSJ/Seeking Alpha 等来源还可能涉及网页条款或非正式接口稳定性。
- `desktop` 的 npm、Cargo、Miniforge、Jupyter 和 Python 依赖有各自许可证；本轮未完成依赖 BOM 法律审核。

状态：源码只验证了依赖和 provider 名称；当前条款为 `UNVERIFIED`。

## 对“即时 AI”的复用建议

| 方式 | 技术建议 | 许可证风险判断 |
|---|---|---|
| 复制 Provider/Fetcher 源码进产品 | 不建议 | 高；直接代码混合 |
| Fork OpenBB 为主底座 | 不建议 | 高；AGPL + 大范围修改/分发 |
| 安装未修改 Python 包供本地个人使用 | 可进入法律/运行核验 | 中；个人使用场景较简单，但产品分发另论 |
| 独立 localhost API sidecar | 首选候选 | 中；边界清晰但不等于免责 |
| 仅借鉴接口模式并独立实现 | 推荐 | 低到中；不得复制受保护表达，需保留独立设计证据 |
| 调用上游/第三方官方 API | 按源逐项评估 | 主要受 API 和数据条款约束 |

## 必须保留的追溯信息

若后续批准试用，应保留：仓库 URL、提交哈希、包版本、许可证全文、修改补丁、依赖锁、provider 条款快照、数据来源 URL 和抓取时间。不得把 OpenBB 源码与其他项目直接混入产品目录。

## 待法律确认

1. 独立 localhost OpenBB API 与闭源/不同许可证桌面客户端的组合边界。
2. 向最终用户打包 Python 环境或桌面 sidecar 的源码提供义务。
3. 未修改 vs 修改 OpenBB MCP/API 的网络交互义务。
4. `desktop/LICENSE` MIT 与根/manifest AGPL 冲突的真实范围。
5. 首批 provider 的数据缓存、原文存档和再分发权限。
