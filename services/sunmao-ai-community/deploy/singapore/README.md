# 社区域名入口维护

社区唯一正式应用仍在北京18808，正式SQLite与原图不迁移。英文根域名在新加坡经18818隧道转发，中文根域名直接进入北京Caddy。两个域名使用同一社区；跨域Cookie和后台登录会话仍各自独立。

## 英文站点配置

新加坡共享`/etc/caddy/Caddyfile`由时变罗盘维护，2026-11-07更新曾移除其中的社区站点。社区现在独立保存为`/etc/caddy/sunmao-community.caddy`，由既有包装文件`/etc/caddy/Caddyfile.instant-ai`追加精确import加载；保持包装文件原有两个import。不要把社区块再次写回共享文件或覆盖其MCP块。

一次性恢复脚本`restore-root-entry.sh`和`sunmao-community-root.Caddyfile.example`放在同一暂存目录，由已授权服务器连接运行。脚本要求现有包装布局、健康隧道及Caddy 2.11+，遇到不同的社区配置或共享文件已有根站点会停止；新建前备份、配置校验，失败恢复包装文件并保留失败配置，重复执行无额外变更。

现场Caddy关闭admin API，默认`systemctl reload caddy`因此不可用。Caddy 2.11+在`caddy run --config`且没有API改配的条件下支持SIGUSR1平滑重读原文件；本次用`systemctl kill --kill-whom=main --signal=USR1 caddy.service`完成，Caddy InvocationID未变。官方说明：[命令与信号](https://caddyserver.com/docs/command-line#signals)。不能对未确认版本或启动模式的进程盲发信号。

本次可恢复备份：`/etc/caddy/backups/sunmao-entry-20261109T085632Z`。只修改了包装文件的追加import并新增社区站点文件；共享配置、即时AI配置和各业务服务保持。

## DNS恢复与验收

所有者2026-11-09进一步确认中文备案通过、英文仍在申请，本轮只把中文作为社区正式入口。中文HTTPS已由`curl --resolve`验证，但权威DNS无A记录。先在已授权阿里云DNS页面核对暂停记录，存在时恢复原记录，不叠加重复记录：

| 域名 | 主机记录 | 类型 | 目标 |
| --- | --- | --- | --- |
| 阿木爷爷.com（xn--4pv19va7008a.com） | @ | A | 47.93.214.76 |

不得改英文根记录、mcp、www、collector、NS委派或创建通配符。英文申请备案的进度不影响本次中文任务，不要求恢复英文DNS。当前会话没有右侧页面点击填写工具；本次未提交DNS变更。没有解析时，定向HTTPS通过不代表普通网址可用。

恢复后从中文普通公网检查`/`、`/health/version`、`/api/size-resources`、24张图片、`/admin/`和未登录`/api/admin/analytics`的401。对比完整生产SHA和资料响应，确认仍是现有数据。操作端发布脚本改以中文正式域名验收，并在任何生产引用变更前检查入口就绪，不通过时快速停止。本次不修改现有生产分支。
