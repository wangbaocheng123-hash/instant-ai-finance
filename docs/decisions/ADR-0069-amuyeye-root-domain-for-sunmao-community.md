# ADR-0069：`amuyeye.com`根域名接入北京榫卯 AI 社区

- 状态：`ACCEPTED / IMPLEMENTING`
- 日期：2026-10-15
- 决策者：产品所有者

## 背景

`阿木爷爷.com`已经代理北京独立社区服务，`amuyeye.com`根域名仍指向新加坡并展示时变罗盘。所有者要求两个根域名都进入同一个榫卯社区，同时明确保护`mcp.amuyeye.com`及其Caddy配置。

## 决定

1. 北京社区Caddy站点只声明`xn--4pv19va7008a.com`和`amuyeye.com`两个精确根域名，统一代理`127.0.0.1:18808`。
2. 不声明`*.amuyeye.com`，不接管`www.amuyeye.com`、`mcp.amuyeye.com`或其他子域名；现有`mcp` DNS和新加坡Caddy保持不变。
3. 切换顺序固定为：验证社区回环健康并应用北京Caddy → 只把AliDNS的`@` A记录改到北京`47.93.214.76` → 等待DNS和证书收敛 → 验证两个根域名的首页、`/health/version`和HTTPS。
4. 若北京Caddy校验、reload或中文域名健康检查失败，应用脚本自动恢复备份；在北京站点准备完成前不得先切DNS。
5. 此域名接入不改变社区Git归属、`sunmao-production`发布边界、数据库或后台凭据，也不重启北京博主采集器、模型下载器和新加坡并列服务。

## 验收

- 本地部署契约测试证明模板只含两个精确根域名、仍直连18808，并保留旧单域站点块升级与失败恢复。
- 生产端必须证明`https://amuyeye.com/`与`https://阿木爷爷.com/`返回同一社区版本；`https://mcp.amuyeye.com/`继续保持原服务，且其DNS记录未被修改。
- 未完成生产验收前，任务状态保持`IN_PROGRESS`。
