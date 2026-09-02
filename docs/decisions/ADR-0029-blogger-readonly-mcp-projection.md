# ADR-0029：博主云端正式原文的只读 MCP 投影

- 状态：`ACCEPTED`
- 日期：2026-09-02
- 决策者：产品所有者

## 背景

北京采集的作品已经可靠传入新加坡即时 AI，豆包识别结果与主人确认的正式原文也保存在 Git 外 `blogger_owner.db`。现有 GPT 博主 MCP 仍只读取 Windows 本地历史库，因此能够在手机页面看到李爱琳rene的新作品和原文，却无法从 GPT 查询同一条云端资料。

## 决定

1. 即时 AI 增加专用机器接口 `POST /api/mcp/blogger/search` 与 `POST /api/mcp/blogger/get`。接口仅允许搜索当前 accepted 作品并读取标题、博主、平台作品 ID、公开抖音链接、日期、处理状态和视频文字。
2. 正式原文优先使用主人保存的 `video_text`；只有正式原文缺失时才返回转写，并明确标记 `transcript_unconfirmed`，不得冒充已确认原文。
3. 接口不返回评论、视频文件、artifact 路径、manifest、机器身份、内部账号、Cookie、主人会话、密钥或财经/罗盘资料，也不提供写入、采集、ASR、AI 或远程控制动作。
4. 接口使用独立 64 位十六进制随机 Bearer 凭据。服务器只从 root `0600` 的 `/etc/instant-ai/blogger-mcp.env` 载入；Windows MCP 只保存当前用户 DPAPI 密文。凭据不复用主人密码、北京传输 HMAC、Codex/ChatGPT 登录或其他项目密钥。
5. Windows 博主 MCP 的原 `search_model_knowledge` 合并本地历史库与新加坡实时只读结果。云端编号固定为 `cloud-video:<work_key>`，并由原 `get_model_knowledge` / `get_video_original` 读取完整文字；云端不可用时本地历史查询继续工作。
6. 不复制新加坡数据库到 Windows，也不建立定时轮询。每次用户发起 MCP 搜索时按需查询，因此新作品完成传输和原文保存后自然可见，没有后台空转。
7. 版本升级为 0.18.0 候选。正式发布仍须所有者明确给出即时 AI 正式发布指令，并使用既有受限发布器。

## 结果与回滚

- 回退 0.18.0 只会移除云端 MCP 投影，不删除或改写任何作品、媒体、评论、识别结果和正式原文。
- 关闭或移除独立凭据后接口 fail-closed 返回未配置，本地 MCP 仍可使用原历史资料。
- 时变罗盘、财经新闻、北京采集调度和豆包计费策略不因本决定改变。
