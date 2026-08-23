# 测试与运行结果

> 项目：`RSSNext/Folo@7c220c69a841defbfeeb00a86ed75ad482b22a57`；来源：`OFFICIAL_ARCHIVE_SNAPSHOT`；目录无 `.git`。

## 本轮结论

`运行未尝试（NOT_ATTEMPTED）`。

本轮只做静态分析。未安装 pnpm 依赖、未启动 Web/Electron/SSR、未连接 Folo API、未执行单元测试/E2E/typecheck/lint、未构建 Windows 安装包，也未修改 upstream。没有任何结果可标记为 `RUNTIME_VERIFIED`。

## 未运行原因

- Folo 位于 R0 第三优先级且是大型 pnpm/Electron/Expo monorepo。
- 快照没有 `node_modules`，安装可能产生大规模网络下载和磁盘占用。
- 根 `.env.example` 的本地 API 后端不在仓库中；使用托管 API 还涉及网络、认证和隐私边界。
- 项目规则要求大型依赖安装先报告成本并等待用户批准。

## 静态测试资产盘点

通过文件名/目录启发式盘点发现约 `142` 个 test/e2e 相关文件，其中 desktop 约 `54`、mobile 约 `52`、packages 约 `15`；该数字只是快照文件统计，可能包含测试辅助文件，也可能漏掉非标准命名测试。根 `package.json` 定义 `test`、`typecheck` 等脚本，桌面包也有构建脚本。

测试文件存在不代表通过。本轮没有读取每个测试断言，也未收集覆盖率、耗时或失败日志。

## 静态构建链检查

| 检查 | 证据 | 结果 | 验证状态 |
|---|---|---|---|
| Windows CI 路径 | `.github/workflows/build-desktop.yml` | 有 Windows 构建、Squirrel/AppX/签名步骤 | `SOURCE_VERIFIED` |
| Node/pnpm 版本 | `.nvmrc`、根 `package.json` | Node 22、pnpm 10.17.0 | `SOURCE_VERIFIED` |
| Electron 入口 | `apps/desktop/package.json` | `./dist/main/index.js` | `SOURCE_VERIFIED` |
| Web 端口 | desktop Vite 配置 | 2233 | `SOURCE_VERIFIED` |
| SSR 端口 | `apps/ssr/index.ts` | 2234 / `0.0.0.0` | `SOURCE_VERIFIED` |
| 实际启动/页面可用 | 无运行记录 | 未尝试 | `NOT_ATTEMPTED` |
| Windows 安装包 | 无本机构建产物 | 未尝试 | `NOT_ATTEMPTED` |

## 获批后的最小验证建议

1. 先记录磁盘、Node/pnpm 与网络环境。
2. 在隔离实验目录安装依赖，记录真实下载/磁盘量和 postinstall。
3. 先执行 typecheck 和选定 packages 单测，再启动 `dev:web`。
4. 明确远端 API 数据边界后再启动 Electron，捕获网络目的地。
5. 验证订阅、条目、搜索、全文阅读、AI 禁用态、本地 DB 和退出清理。
6. 最后验证 Windows Forge 构建、签名缺失时的预期行为和卸载残留。

