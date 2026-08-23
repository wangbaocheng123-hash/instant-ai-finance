# n8n 许可证笔记

> 项目/提交：`n8n-io/n8n` @ `7968432083cdc2526b3b08983d84d0dc73176356`，默认分支
> `master`。这不是法律意见；分发、商用、闭源组合与网络服务必须由合格专业人士确认。
> 研究副本是官方提交归档，无 `.git`、不等于完成克隆；Windows 解压仅排除 `.claude`
> 开发辅助目录。

## 源码复核结论

根 `LICENSE.md` 不是 OSI 常见的 MIT/Apache/GPL，而是 Sustainable Use License 1.0，并先规定：

1. 非主分支（即非 `master`）内容不获许可；本次固定提交来自 `master`。
2. 文件名含 `.ee.` 或目录名含 `.ee` 的源码不受 Sustainable Use License，而须遵守
   `LICENSE_EE.md`。
3. 第三方组件维持其原始许可证。
4. 其余内容受 Sustainable Use License 1.0。

根、CLI、core、workflow、nodes-base、editor-ui 等 package manifest 多数标记
`LicenseRef-n8n-sustainable-use`，与根许可证一致。扫描当前 27,108 文件，约 1,132 个文件落入
`.ee` 命名范围；正式采用必须按实际调用功能逐文件复核，不能仅凭 package 级字段。

## Sustainable Use License 1.0 的关键限制摘要

- 授予非独占、免版税、全球、不可再许可、不可转让的 use/copy/distribute/make available/
  derivative works 权利，但全部受限制条款约束。
- 只能为自身内部业务目的，或非商业/个人用途使用或修改。
- 只有在免费且非商业目的下，才能分发或提供给他人。
- 不得删除/遮蔽许可、版权等 notices；修改副本需显著标记已修改。
- 违反条款会自动终止，文本规定了一次特定条件下的补救机制。

以上是许可证文本摘要，不是对“即时 AI”具体用途的法律定性。

## Enterprise License 范围

`LICENSE_EE.md` 规定 `.ee` 软件生产使用需要有效 Enterprise License；开发/测试可复制修改，
但修改的权利归属和使用/复制/发布/分发/销售仍受文本严格限制。源码中实际例子包括：

- `packages/cli/src/environments.ee`、`evaluation.ee`、`permissions.ee`、`sso.ee`；
- `packages/@n8n/ai-workflow-builder.ee`；
- `packages/@n8n/blob-storage/*s3*.ee.ts`、`*azure*.ee.ts`；
- 多 main、部分 workflow/权限/前端 enterprise 功能。

即使社区版启动路径 import 某些 `.ee` 类，也不能据此推断这些代码已受 SUL 或可自由复用。

## 场景初判

| 场景 | 初步风险 | 建议 |
|---|---|---|
| 用户本人本机运行未修改 community 范围 n8n | 与 personal/internal use 文本较吻合 | 可申请最小运行验证，仍保留 notices |
| 独立 localhost n8n，通过 Public API/Webhook 连接 | 边界相对清楚，但 n8n 的使用/分发条件仍在 | 当前首选候选；不打包前先法律复核 |
| 修改 n8n 核心或 custom build | 需标记修改，且仍受用途/分发限制 | 不建议 |
| 把 n8n/n8n-core 嵌入闭源桌面客户端 | 组合、再分发、不可再许可风险高 | `REJECT` |
| 随“即时 AI”安装包免费分发 n8n | “免费”不自动满足“非商业”；用途与 notices 均需审 | 暂不允许 |
| 使用/复制 `.ee` 生产功能 | 明确需要有效 Enterprise License | 无许可证则 `REJECT` |
| 只参考抽象 workflow/adapter 设计并独立实现 | 风险较低，但要避免实质复制 | `DESIGN_REFERENCE` |

## 第三方与数据来源

根许可证不替代各 npm 依赖、community node、目标 API、网站内容和模型服务条款。442+122 个节点
path 也不意味着所有集成允许抓取、缓存、持久保存或再展示。尤其财经来源、登录 cookie、付费内容
和消息平台必须逐源授权；不得用 n8n 绕过访问限制。

## 对“即时 AI”的许可证边界建议

1. n8n 只作为用户可选、独立安装/运行的 sidecar 候选；代码、数据目录、进程、notices 分离。
2. 主产品不复制 n8n node、UI、execution engine 或 `.ee` 源码。
3. 只通过标准 Public API/Webhook/MCP（后置）交换自有 schema；正式 evidence/database 保持自有。
4. 不创建 n8n Fork；确需补丁时先取得法律结论和用户批准，再建独立 patch/Fork。
5. 在产品分发、商业化、团队/公司内部使用或含 n8n 安装器前重新做场景化法律审查。

许可证状态：`SUSTAINABLE_USE_1.0_WITH_ENTERPRISE_FILE_EXCLUSIONS_SOURCE_VERIFIED`。
