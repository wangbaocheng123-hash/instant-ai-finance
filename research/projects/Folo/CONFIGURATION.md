# 配置

> 项目：`RSSNext/Folo`；提交：`7c220c69a841defbfeeb00a86ed75ad482b22a57`；`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

## 工具链

| 配置 | 源码路径 | 值/作用 | 状态 |
|---|---|---|---|
| Node | `.nvmrc` | `22` | `SOURCE_VERIFIED` |
| 包管理器 | 根 `package.json` | `pnpm@10.17.0` | `SOURCE_VERIFIED` |
| workspace | `pnpm-workspace.yaml` | apps、packages、desktop layers 等 | `SOURCE_VERIFIED` |
| 桌面版本 | `apps/desktop/package.json` | `1.12.0`，Electron `43.1.0` | `SOURCE_VERIFIED` |
| 桌面构建 | `apps/desktop/electron.vite.config.ts`、Forge 配置 | main/preload/renderer 与安装包 | `SOURCE_VERIFIED` |

## 环境地址

`packages/internal/shared/src/env.common.ts` 定义：

- 生产 API：`https://api.folo.is`；
- 生产 Web：`https://app.folo.is`；
- 生产 OTA：`https://ota.folo.is`；
- 开发 API：`https://api.dev.follow.is`；
- 本地 API：`http://localhost:3000`。

`env.desktop.ts` 使用 `@t3-oss/env-core` 校验 `VITE_API_URL`、`VITE_WEB_URL`、`VITE_OTA_URL` 等 renderer 配置。`.env.example` 的本地 API 默认指向 `localhost:3000`，而核心后端不在快照中。

## 文档与源码不一致

`CONTRIBUTING.md` 建议 Electron 开发时设置 `VITE_API_URL=https://api.follow.is`，但当前源码生产默认值为 `https://api.folo.is`。前者属于 `DOC_ONLY` 且与代码不一致：**仅在文档中发现，尚未通过源码验证。** 实际可用域名必须在获批运行时验证，不能把文档值直接写入正式配置。

## 设置持久化与同步

`createSettingAtom` 用 Jotai `atomWithStorage` 把设置写入命名空间存储。设置更新发出 `SETTING_CHANGE_EVENT`，再由上层同步队列选择性同步。AI 设置的 server sync 白名单为空，表示这些设置不会按该白名单同步到 Folo 设置服务；但 BYOK API key/baseURL/headers 仍以客户端设置形式持久化，未见操作系统凭据库加密。

Electron main 另使用 `electron-store` 保存主进程设置。CLI 在用户 home 的 `.folo/config.json` 保存 API URL 和 token。

## 构建与发布配置

`.github/workflows/build-desktop.yml` 对 macOS、Ubuntu、Windows 使用 Node 22 和 pnpm；Windows 路径执行预构建、Vite/Electron Forge、Squirrel `.exe`，并支持 AppX/MS Store 和 SignPath 签名。该文件证明构建意图，不证明本机运行成功。

## 即时 AI 适配要求

- 远端 API 地址、文件库根路径和外部服务凭据必须显式分层。
- 业务库根路径必须强制为 `H:\即时AI文件库`，不可散落到 LocalStorage/userData。
- API key 应放入 Windows Credential Manager/受保护密钥层，不应作为普通 Jotai/JSON 设置。
- 分析、遥测、崩溃报告、Firebase 推送等外联应默认透明且可关闭。

