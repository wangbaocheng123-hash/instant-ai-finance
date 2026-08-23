# R0 研究方法

完整约束以 `FOUNDATION_DIRECTIVE_R0.md` 为准。本文件提供日常执行摘要。

## 顺序

1. 只读环境检查，不自动安装工具。
2. 从六个官方仓库进行 `--depth 1 --filter=blob:none` 轻量克隆。
3. 立即记录默认分支、提交、许可证、版本和下载日期。
4. 先静态阅读入口、核心模块、数据流、存储、扩展、测试和许可证。
5. 再按 TrendRadar、changedetection、RSSHub、OpenBB、Folo、n8n 的优先级尝试运行。
6. 大型依赖、Docker、WSL 或系统修改一律先报告成本并等待用户批准。
7. 统一汇总功能、许可证、架构、集成方案、复用决策和 MVP 蓝图。

## 证据格式

每个重要结论至少记录：

```text
项目：
提交：
源码路径：
类/函数/配置：
调用关系或上下文：
结论：
验证状态：SOURCE_VERIFIED | RUNTIME_VERIFIED | DOC_ONLY | UNVERIFIED
```

## 每仓交付物

每个 `research/projects/<项目>/` 最终必须包含：`SUMMARY.md`、`ARCHITECTURE.md`、`ENTRYPOINTS.md`、`DATA_FLOW.md`、`MODULE_MAP.md`、`DATABASE_AND_STORAGE.md`、`CONFIGURATION.md`、`EXTENSION_POINTS.md`、`REUSABLE_COMPONENTS.md`、`WINDOWS_RUNBOOK.md`、`LICENSE_NOTES.md`、`SECURITY_AND_RISKS.md`、`TEST_RESULTS.md`、`FINAL_ASSESSMENT.md`。

## 完成定义

README 摘要、文件存在或代码搜索命中都不代表研究完成。只有核心调用链已阅读、证据可定位、风险和未知项已列出、评分有理由时，才能标记 `DONE`。

