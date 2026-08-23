# Windows 静态运行手册

> 状态：`NOT_ATTEMPTED`。基线为 `RSSNext/Folo@7c220c69a841defbfeeb00a86ed75ad482b22a57` 的 `OFFICIAL_ARCHIVE_SNAPSHOT`；无 `.git`，未安装依赖、未执行构建或应用。

## 源码确认的前置条件

- Windows 10/11 x64（CI 只证明 Windows 构建路径存在）。
- Node.js 22（`.nvmrc`）。
- Corepack 与 pnpm `10.17.0`（根 `package.json`）。
- 可访问 npm registry、Folo API 和构建所需 Electron/native 预构建资源。
- 桌面开发需要准备 `apps/desktop/.env`，选择有效的 `VITE_API_URL`。

Docker、WSL 不是源码所示桌面开发路径的必需项，不应为本仓验证而安装。

## 建议的获批后验证顺序

以下命令是源码脚本整理，**本轮未执行**：

```powershell
# 仓库根目录
corepack enable
pnpm install

# Web renderer
Set-Location apps/desktop
pnpm dev:web

# 或 Electron
Copy-Item .env.example .env
# 人工校验 .env 中 API 地址后：
pnpm dev:electron
```

Web Vite 配置使用端口 `2233`；`apps/ssr` 使用端口 `2234`。核心本地 API 的示例地址为 `localhost:3000`，但该后端不在快照中，因此完整离线自托管流程无法仅靠本仓启动。

## 构建路径

`apps/desktop/package.json` 提供：

```powershell
pnpm build:electron-vite
pnpm build:electron-forge
pnpm build:electron-forge:ms
```

`.github/workflows/build-desktop.yml` 的 Windows job 使用 Node 22、pnpm、Electron Vite/Forge，产出 Squirrel `.exe`，并有 AppX/MS Store 与 SignPath 签名路径。属于 `SOURCE_VERIFIED` 的构建配置，不是本机成功证明。

## 成本预估与批准闸门

monorepo 包含 Electron、React、Vite、WA-SQLite、Expo 移动端及大量 workspace。静态规划估计完整 workspace 安装可能下载约 `1–3 GB`、占用约 `4–10 GB`，还可能触发原生预构建和 postinstall；这是 `ESTIMATE_NOT_MEASURED`，实际受 pnpm store、缓存、平台包和网络影响。按 R0 安全规则，安装前必须获得用户批准并先记录剩余磁盘空间。

## 停止、检查与清理

- 开发服务器：前台按 `Ctrl+C`；确认端口 2233/2234 无残留。
- Electron：关闭窗口后确认无 Folo/Electron 子进程。
- 清理：只清理本次获批实验生成的明确目录（如对应 `node_modules`、`out`、`dist`），先列出绝对路径并复核；不得修改或删除快照原件。
- 本轮未生成依赖或构建产物，因此没有需要清理的运行残留。

## 已知 Windows 风险

- 根或子包部分脚本含 `rm`、`cp`、`chmod`、shell `export` 等 POSIX 写法；Windows CI 走的是特定脚本组合，不能保证所有本地命令原生 PowerShell 可用。
- Electron/native 依赖和代码签名链可能显著增加下载与故障面。
- `CONTRIBUTING.md` 的 `api.follow.is` 与源码 `api.folo.is` 不一致，运行前必须人工确认。
- released Windows binary 的可用性仅见 README，属于 `DOC_ONLY`：**仅在文档中发现，尚未通过源码验证。**

