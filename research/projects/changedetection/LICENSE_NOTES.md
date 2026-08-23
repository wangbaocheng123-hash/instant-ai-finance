# changedetection.io 许可证记录

## 源码内已验证事实

- 根文件 `LICENSE` 是完整 Apache License 2.0 文本，附录版权行为 `Copyright 2025 Web Technologies s.r.o.`。
- `setup.py` 声明 `license="Apache License 2.0"`。
- `Dockerfile` OCI label 声明 `org.opencontainers.image.licenses="Apache-2.0"`。
- 根目录同时存在 `COMMERCIAL_LICENCE.md`，其开头声称任何涉及第三方商业 “Hosting” 的活动必须执行商业许可，并提供未签署的商业协议模板。
- 顶层未发现 `NOTICE` 文件；`README.md` 的 Third-party licenses 段对 `html_tools.elementpath_tostring` 标出 MIT 来源。
- 本次 `LICENSE` 文件 SHA-256：`47A348E041897A91041F8C4054FE2CEACC400F079F8E08C27A7A28B31EC2E625`。

验证状态：`SOURCE_VERIFIED`。

## Apache-2.0 技术尽调摘要

按根 `LICENSE` 文本，Apache-2.0 通常授予使用、修改、制作衍生作品和分发的版权及专利许可；再分发时需附许可证、标注修改、保留相关版权/专利/商标/归属信息，并遵守专利终止和商标限制。是否存在 NOTICE 义务取决于作品是否包含 NOTICE；此快照顶层未找到 NOTICE。

这只是技术尽调摘要，不是正式法律意见。

## 需要澄清的冲突

`COMMERCIAL_LICENCE.md` 对商业 hosting 声称额外必签要求，但该要求没有写入根 Apache-2.0 文本。仅凭归档快照无法确定：

1. 商业文件是可选支持/替代许可，还是上游主张对某些代码/服务另有约束；
2. 其约束如何与 Apache-2.0 的现有授权同时适用；
3. 个人本地桌面使用、内部服务、对外 SaaS、软件再分发各自的边界；
4. 商标和托管服务品牌是否另有条款。

因此许可证结论必须标为：`Apache-2.0_SOURCE_VERIFIED_WITH_COMMERCIAL_HOSTING_DOCUMENT_CONFLICT_REQUIRING_CLARIFICATION`。

在上游书面澄清或专业法律审查前，不应宣称“商业托管无额外风险”或“商业许可一定有效/一定无效”。

## 对复用方式的影响

| 方式 | 技术建议 | 许可证风险 |
|---|---|---|
| 个人本机独立 sidecar | 优先候选 | 较低但仍保留归属；商业文件边界待确认 |
| REST/RSS API 接入 | 首选 | 减少源码混合；运行/hosting 属性仍需按实际使用判断 |
| 直接 Fork | 暂缓 | 需保留 Apache 文件/归属、标注修改，并解决商业文件冲突 |
| 复制模块进入即时 AI | 不建议 | 增加来源、修改标注、第三方依赖和许可证混合负担 |
| 对第三方提供托管服务 | 未批准 | 正是 `COMMERCIAL_LICENCE.md` 声称覆盖的高风险区域 |

## 第三方依赖

`requirements.txt` 包含 Flask、Apprise、requests、Selenium、Pyppeteer、lxml、OpenAPI Core、LiteLLM、Pydantic 等；本子任务未逐包锁定许可证，也未生成 SBOM。`README.md` 只列出至少一项 MIT 第三方代码，不代表依赖许可证审查完成。

因此最终 `LICENSE_MATRIX.md` 还需要：

- 依赖树与 transitive license 扫描；
- 前端静态资产/flag/icons 的来源；
- Docker image 中 OS packages 的 notices；
- 外部 browser image 许可证；
- 插件和 `EXTRA_PACKAGES` 的逐包许可证；
- 上游对商业 hosting 文档的书面解释。

## 证据索引

| 源码路径 | 内容 | 状态 |
|---|---|---|
| `LICENSE` | Apache-2.0 全文及版权行 | `SOURCE_VERIFIED` |
| `setup.py` | package license metadata | `SOURCE_VERIFIED` |
| `Dockerfile` | OCI Apache-2.0 label | `SOURCE_VERIFIED` |
| `COMMERCIAL_LICENCE.md` | hosting 商业许可声明与模板 | `SOURCE_VERIFIED` |
| `README.md` | Third-party licenses 条目 | `SOURCE_VERIFIED` |

项目/提交：`dgtlmoon/changedetection.io` / `fce24780e74199bf34c62a0d90188cc2fc12f061`。

