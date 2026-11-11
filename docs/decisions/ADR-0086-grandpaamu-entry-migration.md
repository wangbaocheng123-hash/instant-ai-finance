# ADR-0086 迁移到 grandpaamu.com 的独立入口

- 日期：2026-11-11
- 状态：候选。生产路由、证书和 GPT 授权待验收。

| 地址 | 用途和部署位置 |
| --- | --- |
| `https://grandpaamu.com/` | 新加坡即时 AI 主站。现有 `www.grandpaamu.com` 跳转到根域。 |
| `https://grandpaamu.com/mcp` | 新加坡即时 AI 资料 MCP，覆盖博主和模型先生，沿用当前 OAuth 资源及权限。 |
| `https://compass.grandpaamu.com/` | 新加坡时变罗盘网页。 |
| `https://mcp.grandpaamu.com/mcp` | 新加坡罗盘 MCP，替代 `mcp.amuyeye.com`。由独立的 `time-varying-compass` 仓库及固定发布器管理。 |
| `https://collector.grandpaamu.com/` | 北京采集套件统一首页。`/collector/` 访问博主采集，`/model` 访问模型下载，`/health/version` 返回采集器部署版本。 |
| `https://sunmao.grandpaamu.com/` | 英文榫卯社区候选。新加坡 Caddy 经原回环 18818 隧道访问北京社区 18808；`/health/version` 可与中文正式入口对照。 |
| `https://阿木爷爷.com/` | 北京中文社区正式入口。北京 Caddy 直接访问社区 18808。 |

阿里云页面实查 `grandpaamu.com` 原有 `@`、`www`、`compass` 三条启用 A 记录，均为 `47.236.175.118`。罗盘 `mcp` A 记录已指向同一公网地址，HTTPS 待正式发布验证。旧 `amuyeye.com` 区域仅有三条暂停记录：`@` 和 `mcp` 曾指向新加坡，`collector` 曾指向北京 `47.93.214.76`。未找到旧 `www` 的 DNS 记录或独立站点配置。旧英文根域经新加坡访问北京社区，历史配置保留为回滚参考。所有者已暂缓社区英文入口迁移。

## 发布顺序

1. 北京直连须先确认新域名已具备内地接入条件。目前登录的备案账号只显示中文社区已通过，尚无新采集域可直连的证据。如果直连不能使用，先核实现有新加坡受控连接能否提供采集套件所需的路由，保留原北京应用、认证和数据目录。本文的北京 Caddy 模板仅适用于直连，域名指向由生产验证决定。

   网关候选复用现有严格主机校验的受控连接，分别从新加坡回环 18819 和 18817 转发到北京回环 8787 和 18797。新加坡站点从北京模板生成，保留登录、forward_auth、路径重写和私有 API 规则。本机路由和组合 Caddy 校验通过，须在新加坡确认端口空闲和真实转发。采用此方案时，采集域 DNS 指向新加坡，应用仍在北京运行。跨域主人会话需重新登录。
2. 核对两台机器实际运行的 Caddy 入口、独立 import 和已安装发布器。社区新站点由新加坡 `Caddyfile.instant-ai` 导入，复用既有社区隧道。北京直连候选只在其 Caddyfile 追加采集器专用 import。校验完整生产配置并保留回滚备份后，使用各自现有的加载方式。正式应用发布保持项目规定的授权与固定发布器。
3. 在正确网关启用精确 DNS 记录并签发 HTTPS 证书。用正常公网请求核验服务身份、路由和未登录限制。社区中文发布验收继续使用 `https://xn--4pv19va7008a.com/health/version`，英文站点核对同一完整提交。采集候选通过版本预检后才可推进 `beijing-production`，随后使用固定套件发布器并验收 `/`、`/collector/`、`/model` 和版本接口。即时 AI 在采集入口可用后发布新跳转。
4. 罗盘 OAuth issuer、resource、audience、发现元数据、认证挑战及各端点均以罗盘新地址为准。先完成公网 HTTPS、身份、权限和旧地址回滚检查，再由所有者授权原 GPT 连接。沿用原任务、原提示词、指定模型和原事件回环供给。验证真实任务保存、通知及后续关联后，才移除旧连接。遗漏任务先按原任务记录核对并逐项确认补办。

## 已保留的旧引用

- `sunmao-community-root.Caddyfile.example` 保存旧英文根站点和旧罗盘 Service Worker 清理规则，供迁移期回滚审阅。
- 社区 `PUBLIC_HOSTS` 暂时接受精确旧英文根域和中文域。新英文域的写请求也须通过同源检查。其余系统域名继续由测试明确拒绝。
- 旧 ADR、CHANGELOG、TASKS 和 README 中标有历史语境的地址用于追溯。当前迁移和发布入口以本文及操作脚本的新版本为准。
