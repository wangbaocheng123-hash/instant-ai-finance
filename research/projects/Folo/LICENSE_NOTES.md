# 许可证说明

> 对象：`RSSNext/Folo@7c220c69a841defbfeeb00a86ed75ad482b22a57`；研究原件为 `OFFICIAL_ARCHIVE_SNAPSHOT` 且无 `.git`。本文为工程尽调记录，不是法律意见。

## 根许可证

根 `LICENSE` 包含 GNU Affero General Public License v3 全文，并在末尾追加特殊说明：Folo 按 AGPLv3 许可，但 `icons/mgc` 目录内容版权归 MingCute，且“cannot be redistributed”。根 `package.json` 标记 `AGPL-3.0-only`；它没有表达这项资产例外/禁止条款。

`README.md` 许可证段也提到同一限制；`CONTRIBUTING.md` 表示贡献按 AGPLv3 和 README 中特殊例外提供。根 LICENSE 本身已通过源码复核，因此图标限制是 `SOURCE_VERIFIED`，不只是 README 宣传。

## 工程解释

- 仓库主体按 AGPL-3.0 处理：修改并通过网络向用户提供交互服务时，需重点评估 AGPL 第 13 条的对应源码提供义务；分发程序还涉及许可证、版权声明和对应源码等义务。
- `icons/mgc` 不是“额外宽松例外”，而是明确的禁止再分发声明。
- 原样发布包含该目录的源码或二进制资源可能与禁再分发声明冲突。直接 Fork 前必须排除并替换全部相关图标，同时搜索 `i-mgc-*`、移动端生成图标、构建产物和可能的派生资产。
- 由于仓库一面整体声明 AGPL、一面包含不可再分发资产，SPDX 元数据不足以描述实际范围；这使整仓直接复用具有非标准合规不确定性。

## 第三方许可证观察

快照中除根 `LICENSE` 外，还见 `apps/mobile/native/ios/Packages/SPIndicator/LICENSE` 和 `apps/mobile/src/components/ui/qrcode/LICENSE`。仅有少量顶层许可证文件不代表所有 npm/Swift 依赖已完成 notice 审计；正式发布必须从锁文件和打包产物生成完整 SBOM/NOTICE。

## 允许与不允许的初步判断

| 方案 | 初步判断 | 前置条件 |
|---|---|---|
| 仅阅读架构和界面 | 可行 | 不复制受版权保护实现/资产 |
| 独立重写设计模式 | 优先 | 保留干净实现证据，避免逐行派生 |
| 复制少量 Folo 代码到闭源客户端 | 不推荐 | AGPL 兼容性与作品边界需法律复核 |
| 整仓 Fork 并发布 | 高风险 | 完整 AGPL 合规、剔除所有 mgc 资产、第三方 notice、安全整改 |
| 分发 `icons/mgc` | 明确拒绝 | 现有声明禁止再分发；除非另获权利人许可 |
| 调用 Folo 托管 API | 未确定 | 需另查服务条款、隐私、速率、数据使用和可用性；仓库未提供充分证据 |

## 即时 AI 的结论

Folo 不适合作为直接代码底座。可以作为 `UI_REFERENCE`，并优先选择其依赖的上游宽松许可库进行独立评估，而不是复制 Folo glue code。若将来确需采纳任何实现，必须建立逐文件来源、许可证、提交、修改和图标替换清单，并取得法律审查结论。

