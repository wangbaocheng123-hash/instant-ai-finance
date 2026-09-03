# ADR-0032：模型先生只读云端 MCP 与统一即时 AI 资料连接

- 状态：`ACCEPTED_FOR_0_19_0_CANDIDATE`
- 日期：2026-09-03
- 决策者：产品所有者

## 背景

即时 AI 已在单主人页面提供模型先生作品、视频原文、已有投资解读和投资思路，但正式云端 MCP 仍按 ADR-0030 只公开 `search_blogger_videos` 与 `get_blogger_video_text` 两个博主工具。实际 `tools/list` 和生产连接均证明 GPT 不能查询模型先生。所有者明确要求核查并在缺失时补充 GPT/MCP 调用。

## 决定

1. 现有 `https://grandpaamu.com/mcp` 从“博主智能体（云端）”升级为统一的“即时 AI 资料智能体（云端）”。保留原两个博主工具，并新增三个模型先生工具：
   - `search_model_mr_works`：按最新或主题搜索作品；
   - `get_model_mr_work_text`：按 `model-mr-work:` 编号读取完整作品文字和已有投资解读；
   - `list_model_mr_investment_thoughts`：列出或筛选投资思路分类。
2. 模型先生 MCP 使用独立只读投影，只读取 Git 外 `public-snapshot.json` 与 `details/<id>.json`。它不调用模型先生本机 sidecar、媒体接口、标题/原文保存、转写、豆包、AI 问答或任何写操作。
3. 返回字段限定为作品编号、标题、说明、公开原链接、发布时间、关键词、正式原文或明确标记的未确认文字、已有投资解读，以及投资思路的分类字段。禁止返回评论、媒体文件与路径、评论排行、粉丝资料、原始 JSON、数据库字段、Cookie、密钥、本机路径和管理状态。
4. 五个工具全部标记 `readOnlyHint=true`、`destructiveHint=false`、`idempotentHint=true`、`openWorldHint=false`。`official` 才可作为已确认原文直接引用；`video_text_unconfirmed` 和 `transcript_unconfirmed` 必须显式提示需核对。
5. 复用 ADR-0030 的单主人 OAuth、同一资源地址和同一主人账号，不建立第二套账号、密码、域名、仓库或插件。为使所有者已经成功连接的 ChatGPT 客户端无需重复授权，0.19.0 暂时保留兼容 scope 名 `blogger.read`；授权页已改为完整说明博主和模型先生的只读范围。协议 `serverInfo.name` 保持稳定，显示标题升级为统一名称。
6. 版本为 0.19.0 候选。只有所有者另行明确要求正式发布后，才允许通过即时 AI 既有受限发布器上线；发布后在 ChatGPT 连接中点击 Refresh 验证五个工具。

## 理由

统一连接让手机与电脑 GPT 复用已经打通的 OAuth，不再重复建立一套容易混淆的账号和授权流程。专门的文件只读投影比复用完整页面 API 更容易证明不会读取评论、媒体或触发付费动作，也符合 MCP 按用户目标设计工具、分离读写的原则。

## 验收与回滚

- 测试必须证明：模型先生最新/主题搜索、完整原文与解读、投资思路均可读取；无授权调用仍失败；全部五个工具均是只读；博主原有两工具行为不变。
- 使用含评论、媒体路径、本机路径和私有字段的测试资料，确认 MCP 序列化结果不含这些内容。
- 回滚只移除三个模型先生工具并恢复旧显示标题，不删除或改写模型先生、博主、财经新闻或罗盘数据，也不撤销现有 OAuth 客户端。
