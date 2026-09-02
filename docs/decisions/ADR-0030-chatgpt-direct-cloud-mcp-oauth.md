# ADR-0030：ChatGPT 直接连接新加坡博主 MCP 与单主人 OAuth

- 状态：`ACCEPTED`
- 日期：2026-09-02
- 决策者：产品所有者

## 背景

ADR-0029 先建立了新加坡只读 REST 投影，并由 Windows 本地博主 MCP 合并本地历史与云端最新资料。实测发现 ChatGPT 当前安装的“博主智能体”仍连接电脑本地服务；查询李爱琳rene最新作品时返回旧的本地其他博主记录。该结构要求电脑和本地 MCP 在线，不能满足手机端直接查询新加坡即时 AI 的目标。

博主正式原文属于单主人资料。把 MCP 匿名公开不符合资料边界；固定 API Key 又不能由 ChatGPT 自定义连接安全携带，并会违反项目不读取、复制或显示密钥的规则。

## 决定

1. 即时 AI 0.18.0 候选在现有正式域名增加 Streamable HTTP MCP 入口 `https://grandpaamu.com/mcp`，不新建域名、仓库、应用或入站端口。
2. MCP 只提供两个工具：`search_blogger_videos` 搜索当前云端博主作品，`get_blogger_video_text` 读取一条完整视频文字。二者均标记只读、幂等、非破坏、无开放网络动作。
3. 工具只返回 ADR-0029 已批准的博主、标题、公开作品 ID/链接、日期、处理状态和正式原文或明确标记的未确认转写。继续禁止评论、媒体文件、文件路径、manifest、机器身份、Cookie、密钥、财经资料和罗盘资料。
4. MCP 工具使用 OAuth 2.1 authorization-code + PKCE S256。授权页复用即时 AI 现有单主人账号；不增加注册、用户表、邮箱、团队、角色或第二套主人密码。
5. OAuth 通过标准 protected-resource metadata、authorization-server metadata 与 DCR 工作。DCR 只接受 ChatGPT 官方稳定或 callback-id 回调地址，公共客户端不发 client secret；授权响应固定回传精确 issuer，token 固定绑定 `https://grandpaamu.com/mcp` audience 与 `blogger.read` scope。
6. MCP access token 使用现有 Git 外主人认证密钥签名，但采用独立签名上下文和载荷类型，不能作为网页主人 Cookie 使用；网页 Cookie 也不能作为 MCP token 使用。令牌验证包含签名、主人身份、受众、权限、签发时间和过期时间。
7. DCR 客户端和一次性授权码的 SHA-256 摘要保存在 Git 外 `blogger-agent/database/blogger_oauth.db`。授权码五分钟过期且兑换即删除；数据库不保存主人密码、access token 或 API Key。
8. ADR-0029 的独立 Bearer REST 投影暂时保留为 Windows 历史 MCP 的兼容桥，但推荐入口改为 ChatGPT 直接连接云端 MCP。手机端不再依赖电脑开机、本地 8775 端口、DPAPI 配置或定时轮询。
9. ChatGPT Project 只用于整理对话，不负责连接资料。真正的数据入口是同一 ChatGPT 账号中的“博主智能体（云端）”插件/MCP 连接；建立一次后，手机与电脑均从工具菜单选择它。

## 安全与失败边界

- 未授权仍允许 `initialize` 和 `tools/list`，便于 ChatGPT 发现工具及 OAuth 元数据；实际 `tools/call` 必须有有效主人 token，否则返回标准 `WWW-Authenticate` 与 MCP OAuth challenge。
- MCP 不提供采集、下载、ASR、豆包、AI、写入、标题修改、删除或远程控制动作，因此调用不会产生识别费用或改变博主资料。
- OAuth 或 MCP 失败不影响即时 AI 页面、北京传输、财经采集、罗盘、模型先生和本地历史 MCP。
- 回退代码只移除 `/mcp` 与 OAuth 路由；不会删除、迁移或改写博主作品、视频、评论、识别结果和正式原文。

## 验收

- 新增 6 项云端 MCP/OAuth 回归，覆盖 DCR 回调白名单、完整 PKCE 字符集、授权码防重放、token 与网页 Cookie 隔离、audience/scope、工具发现、未授权挑战、GET 行为、完整 HTTP OAuth 交换和授权后只读结果。
- 即时 AI 完整 Python 回归 91 项通过，1 项平台相关 symlink 测试跳过。
- 正式生产仍保持 0.17.0；只有所有者明确要求“正式发布即时 AI 0.18.0”后才允许通过既有受限发布器上线，并在 ChatGPT 开发者模式建立云端连接做真实李爱琳rene原文验收。
