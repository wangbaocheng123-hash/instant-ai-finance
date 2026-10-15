# ADR-0068：榫卯 AI 社区回并原仓库并保持运行隔离

- 状态：`ACCEPTED / IMPLEMENTED`
- 日期：2026-10-15
- 决策者：产品所有者

## 背景

社区曾迁出为独立 Git 仓库，并在北京安装了对应发布器和 GitHub Webhook 事件触发。备案问题并不由仓库拆分解决，反而使新加坡 Codex 的既有“一个仓库管理、通过 `beijing-admin` 控制北京”流程多出一套心智和发布通道。产品所有者明确要求回到原逻辑：社区代码并回 `instant-ai-finance`，但在北京继续与博主采集器并列运行。

## 决定

1. 唯一日常源码恢复为 `services/sunmao-ai-community/`；独立仓库冻结为只读迁移历史，不继续双向同步。
2. 社区继续使用自己的 systemd 服务、18808 回环端口、`/opt/sunmao-community` 发布目录和 `/var/lib/sunmao-community` 数据目录。回并 Git 不等于合并进程或数据。
3. 正式分支继续为本仓库的 `sunmao-production`。只有所有者明确发布后才快进该分支，并从新加坡通过既有 `/home/compassdev/bin/beijing-admin` 一次性启动北京社区发布器；不启用 Git timer。
4. 一次性回切脚本先验证社区和采集器健康，停止独立仓库 Webhook path，把旧裸仓库与修订状态保存到可恢复备份，再安装本仓库的子树发布器。脚本不改当前 release、SQLite、后台密码摘要或 Caddy，也不重启任何并列服务。
5. 独立仓库 `0.3.2` 的产品功能（匿名访问统计、密码后台、刷新按钮）回并；其独立仓库 Webhook 和根仓库发布逻辑不回并。

## 边界

备案与域名可用性是基础设施问题，不改变源码归属。`mcp.amuyeye.com`、即时 AI、时变罗盘、北京博主采集器和模型下载器均不在此次修改范围。只有北京发布器完成、`/health/version` 返回目标修订且并列服务保持健康，才能称为上线。

## 替代关系

本决策替代此前“用独立仓库作为社区唯一生产源”和“在新加坡另建社区备用服务”的未完成方向；ADR-0067 的运行隔离原则继续有效。

## 2026-10-15生产执行与验收

- 回切前实时源为独立仓库，当前release为`babf03b409b6bb3bbfbef85e41abfde4fd95062f` / `0.3.2`；独立Webhook path已失败并保持启用。社区、采集器和模型下载器均active。
- 一次性脚本把旧裸仓库及状态保存到`/opt/sunmao-community/migration-backups/20261015T035313Z`，备份对象连通性检查通过；新裸仓库源精确为`instant-ai-finance.git`。Webhook path随后为`disabled/inactive`，社区Git timer未启用，采集器timer仍`disabled/inactive`。
- `sunmao-production`安全快进至`b5ceea0cc4590892a37ab7db38dfbefb39a28265`后只启动一次社区发布器。发布器完成隔离测试、原子release切换和回环健康检查，最终`Result=success`、`ExecMainStatus=0`；`/health/version`返回`0.3.2`、目标提交及`2026-10-15T03:55:00Z`部署时间。
- `/admin/`为200，未登录后台统计为401，后台密码摘要仍是Git外`0600`文件；匿名统计表存在且访客字段不含IP或完整User-Agent。本地24项社区测试通过，生产发布器再次执行同一测试套件。
- 社区按发布约定重启一次。采集器与模型下载器InvocationID逐项和发布前一致，采集器仍为`10fdb3a` / `1.2.0`；未推进`beijing-production`，未操作下载器、即时AI、时变罗盘、DNS或Caddy配置。外部DNS/备案验收继续作为独立基础设施事项。
