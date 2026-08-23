# Folo 最终静态评估

> 研究基线：官方仓库 `RSSNext/Folo`，默认分支 `dev`，固定提交 `7c220c69a841defbfeeb00a86ed75ad482b22a57`，下载日期 `2026-08-23`，归档 SHA-256 `EA6661B150339412D665E9ECEE45A3CF7E25D0BA87B8DB3621621FED30CBE9AB`。当前只是 `OFFICIAL_ARCHIVE_SNAPSHOT`，目录无 `.git`，**不能算完成 Git 克隆**。全部结论为静态证据，运行状态 `NOT_ATTEMPTED`。

## 推荐结论

- 推荐角色：`UI_REFERENCE`
- 总分：`65/100`
- 主底座：不推荐
- 整仓 Fork：不推荐
- 推荐用法：研究阅读工作台、状态/缓存分层、桌面集成和 AI 交互；按独立许可证选择上游库并自行实现安全、证据和数据层。

Folo 的客户端完成度很高，但它不是包含核心抓取/解析/去重/AI 执行后端的自托管开源闭环。对即时 AI 最有价值的是 UI/交互与客户端架构参考，不是数据平台或财经情报核心。

## 统一评分

| 评价项目 | 得分 | 理由与证据 |
|---|---:|---|
| 与财经情报需求匹配度 | 13/20 | timeline、订阅、阅读、搜索、AI 摘要/翻译重合；`schemas/index.ts` 无财经实体、事件、证据版本，服务端采集缺失。 |
| 已有功能完整度 | 11/15 | 桌面/移动/CLI/SSR/AI UI 完整；`api-client.ts` 显示核心能力依赖仓库外 API，无法自托管闭环。 |
| 代码可维护性 | 8/10 | TypeScript monorepo、领域 store/service、共享包和测试资产结构清楚；体量大且多状态层/平台层耦合。 |
| 扩展和适配能力 | 7/10 | 有 SDK、IPC、CLI、OPML、Webhook、MCP、RSSHub、integrations；无完整第三方运行时插件 ABI，后台执行缺失。 |
| Windows 本地运行能力 | 8/10 | Electron、Windows CI、Squirrel/AppX/SignPath 路径明确；本机未运行，依赖大且部分 shell 脚本偏 POSIX。 |
| 数据来源能力 | 5/10 | 客户端支持 Feed、OPML、RSSHub 管理；实际抓取器、解析器和调度器不在快照中。 |
| AI 与过滤能力 | 7/10 | 摘要、翻译、聊天、AI tasks、Action UI/调用点丰富；模型执行、过滤执行和调度服务端不可见。 |
| 上游活跃度 | 3/5 | 快照为现代 Electron/React 技术栈且 README 有发布信息；无 `.git`，不能复核真实提交历史/活跃度，相关说法 `DOC_ONLY`。 |
| 许可证适配性 | 1/5 | 根为 AGPL-3.0，且 LICENSE 明确 `icons/mgc` 禁止再分发，整仓复用合规风险高。 |
| 改造成本 | 2/5 | 需替换图标、补后端/证据库/H 盘布局、消除远端平台依赖并完成 Electron 安全整改。 |
| **合计** | **65/100** | **适合作为 UI 参考，不适合作为主底座。** |

## 决策依据

### 可取

- `BootstrapManager`/`AppManager`/`WindowManager` 展示了成熟桌面壳职责拆分。
- `EntrySyncServices`、morph、领域 store 和 SQLite service 展示了远端同步到本地缓存的清晰主链。
- 时间线、阅读器、Fuse 搜索、AI 聊天/摘要/翻译和系统通知具有较高产品参考价值。
- Windows Forge/CI/OTA 路径完整，可帮助比较未来桌面壳工程成本。

### 否决直接复用的事实

1. `packages/internal/shared/src/env.common.ts` 与 `api-client.ts` 证明生产主链依赖 `api.folo.is`。
2. 仓库内没有核心业务后端；`apps/ssr`、`apps/ota`、根 `api` 都不是采集/去重/AI 执行服务。
3. `EntryService.getEntriesToHydrate` 清理旧缓存；DB 不满足长期证据保存。
4. `WindowManager` 启用 Node integration 并关闭 sandbox/context isolation，需重大安全整改。
5. 根 `LICENSE` 对 `icons/mgc` 追加不可再分发限制。

## 对即时 AI 的落地建议

- 保留 Folo 为阅读器/UI/桌面工程参考样本，不把源码混入正式仓库。
- 直接研究并依法选用 Mozilla Readability、Fuse.js 等上游库，而不是复制 Folo 封装代码。
- 正式数据层另建在 `H:\即时AI文件库`，支持原文、hash、采集批次、证据版本、实体/事件和备份。
- 数据采集优先评估 RSSHub、changedetection 等独立上游；Folo 不承担采集底座。
- 若未来选 Electron，必须以安全默认值重新设计 preload/IPC/URL fetch，而非继承现有窗口配置。
- 不分发 `icons/mgc`，不在法律审查前复制 AGPL 实现。

## 未决与运行闸门

- 托管 API 的服务条款、数据处理、速率、后端开源性：`UNVERIFIED`。
- README 所述 Windows 发布包和活跃状态：`DOC_ONLY`，**仅在文档中发现，尚未通过源码验证。**
- Windows 依赖安装、启动、测试、构建、网络目的地、资源占用：`NOT_ATTEMPTED`。
- 若要提升证据等级，需用户批准大型 pnpm/Electron 依赖安装和远端 API 网络验证。

