# n8n 安全与风险

> 项目/提交：`n8n-io/n8n` @ `7968432083cdc2526b3b08983d84d0dc73176356`。
> 这是静态风险评估，不是渗透测试、依赖漏洞扫描或安全认证。
> 研究副本是官方提交归档，无 `.git`、不等于完成克隆；Windows 解压仅排除 `.claude`
> 开发辅助目录。

## 高风险项

| 风险 | 源码证据 | 影响 | 最低控制 |
|---|---|---|---|
| SSRF 默认关闭 | `ssrf-protection.config.ts::SsrfProtectionConfig.enabled=false` | 用户可控 HTTP/导入/OAuth URL 可能访问内网/metadata | localhost、开启 SSRF、allowlist、网络 egress 控制 |
| 默认监听 `::` | `GlobalConfig.listen_address='::'` | 可能暴露到所有接口 | 显式 `N8N_LISTEN_ADDRESS=127.0.0.1` |
| 任意外部连接器与 credential | nodes-base + CredentialsEntity | 数据外发、权限放大、供应链/API 风险 | 节点 allowlist、最小权限 credential、审计 |
| Code/Python 节点 | `Code.node.ts`；TaskRunnersConfig 默认 internal | 用户代码执行与资源滥用 | MVP 禁用 Code；外部隔离 runner 另评估 |
| 社区 npm 节点 | `CommunityPackagesService.downloadPackage` | 引入第三方代码/依赖；虽 ignore scripts 仍会执行 node runtime code | 默认禁未验证包、固定 checksum、人工审查 |
| Webhook/MCP 暴露 | `AbstractServer.start`、`McpTrigger.webhook/McpServer` | 未授权调用、tool/credential 越权、session 滥用 | 默认不暴露外网；auth、rate limit、tool allowlist |
| execution 保存敏感数据 | `ExecutionData.data/workflowData`；默认成功/失败均保存 | 原文、模型输入输出、PII/credential 派生内容留存 | 缩短 retention、字段脱敏、正式证据另存 |
| 加密 key 运维 | `Cipher` + `InstanceSettings` | key 丢失无法解密，泄漏导致凭据暴露 | 稳定密钥安全存储、备份和访问权限 |
| 大供应链面 | `pnpm-lock.yaml` 与 27k 文件 | 漏洞、恶意包、更新成本 | SBOM、lockfile、vulnerability/license scan；未完成 |
| `.ee` 混合授权 | `LICENSE.md` + 1,132 scoped files | 误用未授权生产功能 | 禁用/隔离 enterprise 功能，逐调用链复核 |

## 已见防护（不等于安全完成）

- `SecurityConfig` 默认限制文件节点到 `~/.n8n-files`，阻止 n8n 内部目录与 `.git`；
- SSRF 实现预置 private/loopback/link-local/metadata CIDR，并说明 DNS/connect/redirect 多阶段检查；
  但总开关默认关闭、调用点还需选择 `ssrf`；
- `Cipher` 对 credential data 使用实例密钥加密，并有 GCM-wrapped DEK 的轮换路径；
- `Server.configure` 注册 API key 与 session auth 策略，类型文件受 auth middleware 保护；
- Express 使用 Helmet/CSP 相关配置，webhook/form HTML 有 sandbox CSP；
- community package 支持 checksum、`--ignore-scripts`、安装回滚和 registry license gate；
- execution 有 credential permission check，workflow 运行前检查；
- graceful shutdown、execution timeout、concurrency 与 pruning 均可配置。

## 财经情报专项风险

1. n8n 不提供来源许可、robots/terms、付费墙、原文留存期限等治理模型。
2. AI node 会把文本发送到所选模型供应商；需按来源授权、隐私和数据出境策略裁剪。
3. 通用 workflow 可被误配为自动交易或买卖通知；项目必须禁止交易执行节点/凭据，输出标为研究信息。
4. RSS Trigger 只按时间游标判断更新，不能作为一次且仅一次证据；正式入库要有自有幂等键和审计。
5. workflow 作者可以绕过预期 adapter 直接 HTTP/Code/DB；生产实例必须限制节点与权限。

## Windows 与本机部署风险

- C 盘可用空间有限，monorepo install/native build 可能耗尽空间；
- `.n8n/config`、SQLite 和 storage 默认落用户目录，若未配置会违反 H 盘业务数据边界；
- Windows 文件权限、杀毒软件、长路径、native addon 编译行为未实测；
- 端口 5678 和 runner 5679 需确认只监听本机且不被防火墙例外放开。

## 采用前阻断项

- 完成发布包的来源/哈希/版本锁定和最小 Windows 运行；
- 生成依赖 SBOM、许可证与漏洞扫描；
- 证明所有数据、日志、SQLite、binary、cache 落 H 盘；
- 建节点 allowlist，禁 Code、文件写、shell/SSH/DB、community install、MCP server 等高风险项；
- 对 SUL/Enterprise 及实际分发场景完成法律确认；
- 不使用真实交易账号、券商 API 或自动买卖节点。
