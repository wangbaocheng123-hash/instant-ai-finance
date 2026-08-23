# 六仓许可证矩阵

状态：`SOURCE_REVIEW_COMPLETE / LEGAL_REVIEW_REQUIRED`。本文件是固定提交的工程尽调，不构成法律意见；“允许”均以遵守许可证、第三方依赖和数据源条款为前提。

| 项目 | 源码核验结论 | 修改 | 个人本地使用 | 分发/商用 | 网络服务条款 | Fork/嵌入判断 | 推荐边界 |
|---|---|---|---|---|---|---|---|
| TrendRadar | GPL-3.0；only/or-later 未由 SPDX 锁定 | 允许，传播修改版有强 copyleft 义务 | 通常可行 | 可行但分发需对应源码、通知等 | GPLv3 本身无 AGPL 式网络触发条款 | Fork 可行但会约束产品许可；不同许可核心内嵌风险高 | 条件 Fork 或独立服务；先决定发布模型 |
| RSSHub | AGPL-3.0 | 允许，修改和传播受 AGPL | 通常可行 | 可行但义务强 | 修改版经网络交互需重点处理对应源码提供 | 不建议复制路由/核心进不同许可证客户端 | localhost/独立服务，保留来源和许可证 |
| changedetection.io | 根 LICENSE/metadata 为 Apache-2.0；另有 `COMMERCIAL_LICENCE.md` 声称第三方商业 hosting 需商业许可 | Apache 文本允许，但冲突文件范围待确认 | 个人 sidecar 风险相对较低 | 商业 hosting 尤其待澄清 | Apache 本身无网络条款；商业文档另行声称 hosting 限制 | 暂不 Fork/复制核心，直到上游或法律澄清 | REST/RSS 独立 sidecar |
| OpenBB | 根、core、CLI、Provider、manifests 为 AGPL-3.0-only；`desktop/LICENSE` MIT 与同目录/根 AGPL 信号冲突 | 允许但强 copyleft | 可进入个人本地评估 | 分发/服务需 AGPL 和数据条款审查 | AGPL 第 13 节相关 | 不复制 Provider/模型；完整 Fork 不建议 | 最小 localhost API 或受控库依赖；逐 Provider 审条款 |
| Folo | AGPL-3.0-only；根 LICENSE 明确 `icons/mgc` 不可再分发 | 主体可改；禁再分发资产必须排除 | 仅研究/运行仍需遵守条款 | 整仓分发高风险，图标限制非标准 | AGPL 网络交互义务 | 不建议整仓 Fork，也不复制 glue code/资产 | 只作 `UI_REFERENCE`；独立重写模式 |
| n8n | Sustainable Use License 1.0；约 1,132 个 `.ee` 范围文件另受 Enterprise License | 仅在许可用途/限制内；`.ee` 生产使用需有效许可 | personal/internal 场景相对吻合 | 免费不等于可商业分发；再分发/嵌入高风险 | 不是 AGPL，但用途与 make available 受 SUL 限制 | 拒绝 Fork/内嵌/复制节点；Enterprise 功能未授权则拒绝 | 本机可选独立 sidecar，经 Public API/Webhook 接入 |

## 许可证对组合架构的直接影响

1. 不把六仓源码混入同一个产品模块。
2. AGPL/GPL/SUL 项目优先采用进程和数据目录分离的 API/sidecar 边界；该边界只降低代码混合，**不自动消除许可证义务**。
3. 使用场景已固定为本机所有者个人使用且当前不分发/商业化，显著降低当前传播场景风险；但 TrendRadar 仍因运行、数据模型和上游依赖问题保留为 `CORE_FORK_CANDIDATE`，不能仅凭个人使用升格 `PRIMARY_BASE`。
4. Folo 的 `icons/mgc` 不进入产品、模板、截图资产或安装包。
5. n8n 的 `.ee` 文件和 Enterprise 功能不复制、不启用、不打包，除非取得相应许可。
6. 金融数据“代码可用”不等于“数据可归档/再分发”；OpenBB Provider、RSSHub 路由和所有网页来源都要逐源记录条款、授权、抓取时间和原始 URL。

## D0 前必须确认

- 已确认“即时 AI”仅供本机所有者本人使用，当前不向他人分发；未来若改变必须重新进行许可证审查。
- 是否接受 GPL/AGPL 对主程序或网络服务的对应源码义务。
- changedetection.io 商业托管说明与 Apache-2.0 根许可证的适用关系。
- OpenBB `desktop/LICENSE` 的 MIT 范围，以及首批 Provider 的缓存与再分发权。
- Folo 禁再分发资产的完整引用范围。
- n8n 只允许本机独立安装运行，不随产品向他人分发；未来改变时重新审查。

采用前还需生成依赖 SBOM、第三方 NOTICE、数据来源条款快照和逐组件许可清单。
