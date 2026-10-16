# ADR-0069：`amuyeye.com`通过新加坡安全入口接入北京榫卯 AI 社区

- 状态：`ACCEPTED / LIVE`
- 日期：2026-10-15
- 决策者：产品所有者

## 背景

`阿木爷爷.com`已经代理北京独立社区服务。所有者要求`amuyeye.com`根域名也进入同一个社区，同时明确保护`mcp.amuyeye.com`的DNS和Caddy配置。直接把英文根域名指向北京后，现场证明阿里云轻量服务器会在Caddy之前对未备案域名返回`Beaver 403`；仓库拆分或增加北京站点块都不能绕过备案。

`amuyeye.com`此前在新加坡展示时变罗盘，时变罗盘已有独立正式地址`compass.grandpaamu.com`。新加坡又已经持有严格主机密钥校验的北京管理员连接，因此可以只复用其SSH传输能力，不复制社区应用或数据库。

## 决定

1. AliDNS的`amuyeye.com`根记录保持新加坡`47.236.175.118`。`mcp`记录继续保持同一原地址，`collector`记录不变。
2. 新加坡运行低权限`sunmao-community-beijing-tunnel.service`，以`compassdev`和既有Git外SSH身份建立`127.0.0.1:18818`到北京`127.0.0.1:18808`的本地转发。启用严格主机密钥、`ExitOnForwardFailure`、无agent、无ProxyJump和自动重连；不开放`GatewayPorts`。
3. 新加坡Caddy只声明精确`amuyeye.com`根域名并代理`127.0.0.1:18818`。不声明`*.amuyeye.com`，不接管`www`或`mcp`；应用继续在北京运行并使用同一SQLite、后台密码摘要和发布分支。
4. 根域名过去的罗盘Service Worker可能继续向旧浏览器提供离线页面。精确根站点提供无缓存的替代`/service-worker.js`，激活后删除该origin旧Cache Storage并让窗口回到网络；不得未经主人确认直接删除浏览器站点数据。
5. `阿木爷爷.com`继续由北京Caddy直连18808，备案拦截单独处理。时变罗盘继续使用`compass.grandpaamu.com`；即时AI、MCP、北京采集器和模型下载器的应用边界不变。

## 2026-10-16补充：中文入口暂缓、发布验收解耦

所有者明确要求在备案完成前暂不使用中文域名。当前`阿木爷爷.com`没有可用DNS解析，因此上文第5项仅保留历史北京站点配置，不代表中文公网入口已可用。电脑端及新加坡云端发布脚本统一以`https://amuyeye.com/health/version`核对完整目标提交，不再等待中文域名。备案完成后另行验证DNS、HTTPS和同一社区服务再接入；本次不改DNS、Caddy、隧道、应用部署位置或其他服务，不把公网可达等同于备案手续已完成。

## 原入口配置回滚

新加坡Caddy修改前保留时间戳备份。若隧道、Caddy校验、根域HTTPS或社区版本检查失败，恢复Caddy备份、停用并移除隧道单元，再重启经过校验的原Caddy配置。北京端任何临时英文站点块必须恢复为原中文单域配置。

## 验收

- 隧道回环和新加坡Caddy回环均返回北京社区`0.3.2`与提交`b5ceea0cc4590892a37ab7db38dfbefb39a28265`。
- 从北京外部网络访问新加坡根域：首页200，`/health/version`返回同一社区版本；证明不是只在新加坡本机成立。
- `mcp` DNS保持`47.236.175.118`，其Caddy站点块摘要前后一致且HTTPS仍响应；`compass.grandpaamu.com`仍进入既有登录。
- 北京社区、采集器和模型下载器进程未因域名切换重启；未创建第二份社区应用、数据库、定时Git轮询或通配符DNS/Caddy规则。
