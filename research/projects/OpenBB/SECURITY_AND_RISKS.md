# OpenBB 安全与风险

研究锚点：`OpenBB-finance/OpenBB`，提交 `3e071fcc2cd9f891cac6040ae60296dba76dab46`，`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

## 风险摘要

OpenBB 适合作为本机受控数据服务，但默认配置面向开发便利：核心 API 认证默认关闭、CORS 默认全开放、凭据可明文写 JSON，MCP 可暴露大量金融工具。若绑定非 localhost 或直接纳入桌面发行，风险明显上升。

## 高优先级风险

### 1. API 默认无认证且 CORS 为 `*`

- `core/openbb_core/env.py::Env.API_AUTH` 默认 false。
- `core/openbb_core/app/model/api_settings.py::Cors` 的 origins/methods/headers 默认均为 `['*']`。
- `core/openbb_core/api/auth/user.py` 只在开启 `OPENBB_API_AUTH` 时创建 HTTP Basic security。

影响：若错误绑定 `0.0.0.0`，同网段或浏览器上下文可能访问数据接口。缓解：只绑定 `127.0.0.1`，显式认证，收紧 CORS，Windows 防火墙验证。状态：`SOURCE_VERIFIED`。

### 2. Provider 凭据明文落盘

- `UserService.write_to_file()` 将 credentials 写入 `~/.openbb_platform/user_settings.json`。
- 桌面 `credentials.rs::update_user_credentials_impl` 也直接序列化 JSON。
- Pydantic `SecretStr` 只保护内存显示，不加密文件。

影响：本机其他进程、备份、错误上传或日志采集可能获得 keys。缓解：进程环境/安全凭据服务、最小 ACL、日志脱敏、禁止 Git。状态：`SOURCE_VERIFIED`。

### 3. MCP 工具暴露和外部提示/skills

- `MCPSettings.default_tool_categories` 默认 `['all']`。
- 可开启 tool discovery，并可加载多种 vendor skills providers。
- `server_auth` 默认为空；`TokenAuthProvider.authorize()` 在未配置时直接允许。

影响：被非预期客户端连接时可调用大量金融数据接口；外部提示/skills 增加提示注入和权限扩张面。缓解：localhost、认证、类别白名单、默认关闭 discovery/vendor skills、限制文件路径。状态：`SOURCE_VERIFIED`。

### 4. Desktop 执行和安装系统命令

- `desktop/.../backends.rs` 保存命令字符串并最终通过 `cmd`/`bash` 启动；调用 `validate_command_input()` 过滤危险模式。
- `command_sanitizer.rs` 是正则/文件启发式，不是 OS 沙箱。
- `startup.rs` 下载并运行 Miniforge installer；`uninstall.rs` 会停止进程、运行卸载器和清理环境。
- Tauri config 的 CSP 为 `null`。

影响：桌面是高权限本机进程管理面；配置篡改、供应链或 sanitizer 绕过均可能导致命令执行。缓解：不 Fork 为产品壳；若试运行，仅官方签名包/校验、普通用户权限、严格后端命令白名单和独立安全审计。状态：`SOURCE_VERIFIED`。

### 5. 供应链和 provider 条款

快照包含 32 provider、17 extensions，大量 Python/npm/Cargo 依赖；桌面还查询 GitHub latest Miniforge 资产。不同 provider 的 key、速率、网页抓取和数据再分发条款不同。

影响：依赖漏洞、上游变更、数据许可违规、接口失效。缓解：最小安装、锁版本、SBOM、来源白名单、provider 条款登记、不要自动升级关键采集链。状态：依赖面 `SOURCE_VERIFIED`；当前漏洞/条款 `UNVERIFIED`。

## 中优先级风险

- **API key 出现在 URL。** FMP Fetcher 把 `apikey` 拼入 query string；代理、debug 和异常日志若记录 URL 可能泄漏。`SOURCE_VERIFIED`。
- **无中心证据完整性。** 缓存没有统一 hash、WORM 或来源快照，不能作为投资证据库。`SOURCE_VERIFIED`。
- **结果正确性依赖供应商。** 同一标准模型隐藏 provider 差异；标准化不等于语义完全一致。`SOURCE_VERIFIED`。
- **动态扩展执行。** Python entry points 在加载时执行安装包代码；只允许可信、锁定扩展。`SOURCE_VERIFIED`。
- **自动 build 副作用。** `OPENBB_AUTO_BUILD` 默认 true，import 可生成/清理 package assets；实验必须隔离。`SOURCE_VERIFIED`。
- **开发路由。** DEV_MODE 下会加载 auth/system/user 路由；`/user/me` 返回 UserSettings，不能对外暴露。`SOURCE_VERIFIED`。
- **更新器与在线安装。** Tauri updater 和 Miniforge latest URL 引入远程供应链；需签名/校验流程审计。`SOURCE_VERIFIED`。

## 功能性风险

- 没有调度/去重/AI/推送闭环，若误选主底座会产生大量补建工作。
- 没有中心业务数据库，cache 的结构和生命周期不统一。
- 中国市场、紫金矿业公告、黄金/铜产业链权威中文源覆盖尚未实测；现有 provider 列表不能证明满足目标。
- 上游活跃度与提交对应 release 无法由无 Git 的归档验证。

## 风险处置建议

1. 只把 OpenBB 当可替换的数据边界，先用 subprocess/localhost REST adapter。
2. 先验证 2–4 个无 key/官方 provider，不安装 `[all]` 和桌面。
3. 统一将 OpenBB 输出再写入“即时 AI”自有 schema、去重、证据与审计层。
4. 供应商响应必须记录 provider、请求参数、采集时间和原始 URL。
5. 完成 AGPL 与 provider 条款审查后再决定是否随产品分发。
6. 任何真实运行都先定义 H 盘路径、磁盘预算、日志脱敏和清理清单。

以上均未实施，状态 `UNVERIFIED`。
