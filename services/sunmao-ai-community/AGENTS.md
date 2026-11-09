# 榫卯 AI 社区资料任务指令

本文件适用于 `services/sunmao-ai-community/`。先遵守仓库根目录 `AGENTS.md` 的启动顺序、服务隔离和发布规则，再执行以下资料流程。

## 当前发布验收入口

- 所有者于2026-11-09明确选择已备案的`阿木爷爷.com`为社区正式入口；电脑端与新加坡云端均以`https://xn--4pv19va7008a.com/health/version`返回的完整`repository_revision`验收目标提交。发布脚本先核对中文公网服务就绪，失败时在推进生产分支前停止，不能只凭Git推送或定向访问成功宣称上线。
- 中文北京站点定向TLS已通过，根域DNS恢复及普通公网验收尚未完成；只恢复中文`@ A 47.93.214.76`，先核对实际记录，优先启用原记录而非新建重复项。英文`amuyeye.com`仍在备案申请中，本轮不改其DNS、不接入北京、不把其恢复作为中文任务依赖。永久不得影响`mcp.amuyeye.com`或添加通配符。入口未验收前不要把备案通过等同网址已恢复。
- 新加坡英文站点由`/etc/caddy/Caddyfile.instant-ai`独立import `/etc/caddy/sunmao-community.caddy`，不要写回会被时变罗盘更新的共享Caddyfile。关闭admin端口时，现场Caddy 2.11+用SIGUSR1平滑加载；操作手册见`deploy/singapore/README.md`。
- 沿用既有新加坡`/home/compassdev/bin/beijing-admin`，在北京执行一次`sudo -n systemctl start --no-block sunmao-community-git-deploy.service`。不得新建定时轮询，不得调用采集套件发布器。普通修改仍停在`main`，不触发生产发布。

## 接到所有者从手机或新加坡云端 Codex 提供的新资料时

1. 先读 `docs/KNOWLEDGE_INTAKE_RUNBOOK.md`、`config/knowledge_taxonomy.v1.json`、`docs/CLOUD_KNOWLEDGE_LIBRARY.md`，再核对根目录 `STATUS.md` 和 `TASKS.md` 中知识库接口的实际完成状态。不得把规划中的云端上传、检索或 AI 接口当作已经上线。
2. 按“一个主题条目可关联多种资料”分类：选一个 `primary_topic`，再选零个或多个 `resource_kinds`。`尺寸型`和`榫卯108道`是页面视图，不是互斥资料库；具体规则以 runbook 和 taxonomy 为准。
3. 先查现有稳定 ID、精确来源 URL、集数和内容摘要。相同来源或同一主题的补充资料应关联/修订已有条目，不能随意建立重复条目；保留原链接和来源定位。
4. 所有者直接提供的标题、尺寸和说明可按其原意录入并标记“所有者提供”。从网页、视频、图片或模型提取出的事实要保留可核查出处；无法确认单位、比例、材料或授权时，标记待核实，不得生成确定尺寸或宣称已经理解视频/模型。
5. 北京社区 `/var/lib/sunmao-community` 是知识正式数据位置。新加坡即时 AI 数据目录、本地电脑、Obsidian 笔记和公开 Git 仓库均不是线上知识库。原始媒体、数据库、草稿原文、凭据和备份不得写入 Git。
6. 0.3.10生产已有知识表、Git外图片与授权文章导入器、公开只读尺寸API和站内文章阅读。所有者授权复制的文章只能保存白名单正文块和本地图片，保留出处，视频仅外链。使用`python3 -m scripts.import_size_articles`，显式指定数据库及知识目录，校验清单SHA-256；相同导入可安全重试，遇到同ID内容不同、来源或顺序冲突立即停止，不覆盖旧资料。完整知识后台仍未实施；不得手工改生产SQLite、公开映射数据目录，或把本地暂存说成北京正式入库。
7. 所有者明确要求“加入/发布资料”时，未来内容后台可以按审核规则直接更新知识内容；代码改动与 Git 静态清单的正式上线仍须遵守根目录社区发布规则。报告必须区分“已整理”“已进入 main”“已写入北京知识库”“公网可见”四种状态。

分类有歧义时先记录确定的来源和候选类目，继续能独立完成的步骤；只就会改变事实或公开范围的缺项向所有者提一个简短问题。
