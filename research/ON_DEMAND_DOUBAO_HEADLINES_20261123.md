# 前台按需豆包标题翻译：实现与隔离验收

2026-11-23；即时AI线上0.34.1，本轮0.35.0候选，用户已明确授权豆包API使用，正式发布仍单独执行。

## 已有能力与差距

- 原translation.py复用MyMemory/可选LibreTranslate，已有item_translations/translation_usage、请求锁、额度及原题匹配缓存；只有HTTP调用会翻译，不在新闻采集中运行。
- 原InstantFinanceApp.refresh每60秒触发translateVisible，候选包含最新16条、热点16条及全部频道前2条，没有检查页面visible或真实屏幕交集。详情另有独立翻译请求；换成收费提供器前需收紧这些边界。
- 现有方舟文本适配器model_mr_keywords.py使用固定Responses地址及服务端环境凭据。本轮仅查systemd EnvironmentFiles路径，确认既有model-mr-text-secrets.env仍被挂载；没有读取/导出凭据内容、照抄截图密钥或修改该文件。截图授权是本任务API使用授权，不等于已验证每个密钥/模型权限。

## 复用与实现证据

仓库为wangbaocheng123-hash/instant-ai-finance，本轮基于e4bb34f检查点的既有Python/SQLite、urllib及前端；没有引入SDK、大型依赖或第三方源码。

1. headline_translation.translate_headline：固定模型doubao-seed-translation-250915和ark.cn-beijing.volces.com/api/v3/responses，单个input_text配translation_options(en→zh)，只接受completed消息里的output_text。完整标题超过1000 UTF-8字节不截断提交；浏览器先跳过这类标题。20秒请求超时、64KiB响应上限，不重定向、不重试、不返回原始错误体。
2. translation.headline_provider只用于标题状态及translate_items；active_provider/translate_text/reader_translation继续原摘要流程。新提供器可复用既有安全保存的方舟文本凭据，独立标题密钥环境变量优先；不触碰关键词模型ID或ASR。
3. translate_items仅按本次ID取新闻，并按当前请求涉及的原题查短期译文，复用同题缓存、为新条目另存副本，原题变更不会混用。模型请求仍受全局锁及软件日源字符上限保护；费用不明的失败计入尝试用量，不假称平台退款或免费。
4. VisibleTitleTranslation使用真实IntersectionObserver、document.visibilityState、pagehide及汉化状态。每次只发1条，提交前再检查交集/当前元素与遮罩，先显示原题、再原位更新，不重建列表。隐藏/关闭后不提交下一条，已接受的一条可完成。一次页面会话中同题只尝试一次，错误会停止队列，不自动重试；主人重新开启可手动再尝试。
5. FinancePanel标题、热点标题与详情H1统一标记；详情不再单独绕过队列。只更新中文与原题节点，保留滚动上下文。刷新、换频道和返回前台重建观察，不对隐藏频道或屏幕外标题预译。
6. POST/api/translate必须有visible_only=true，并最多受理一个ID/一条新译，原主人认证和X-Instant-AI保护保持。旧UI无标记即400，不让旧的后台12条请求批量进入新提供器。该字段只是客户端协议，不声称服务端能独立读取手机锁屏状态。

## 官方契约

- [模型列表](https://docs.volcengine.com/docs/ark/model-list?lang=zh)：该专用模型及输入长度限制。
- [创建Responses](https://docs.volcengine.com/docs/ark/create-model-responses-api?lang=zh)：文本输入、translation_options、输出消息/output_text。
- 所有者提供截图中的选型为Doubao-Seed-Translation/250915，示例同一路径及source_language/target_language。实现按相反方向en→zh，未引入聊天提示批量拼接、多轮记忆或外部资料服务。

## 最终检查

- 新增9项：单标题契约与不带摘要/URL、未配置0调用无免费回退、同题跨ID/重开缓存及删除原条后保留副本、原题变更、敏感错误/不完整响应不缓存、完整标题计数/日上限、摘要提供器不变、重定向拒绝、旧客户端拒绝/认证及单条限额。
- 最终完整364项Python通过，2项可选跨仓集成因缺COMPASS_SOURCE_ROOT跳过；没有把可选集成冒称已运行。首轮因打包SW尚未同步出现版本契约失败，完成正式静态同步后完整重跑通过。
- 10项既有前端契约、TypeScript/Vite 0.35.0构建、10项包内静态与dist逐字节一致通过。现有界面品牌/博主布局与媒体依赖保持。
- 新增合成浏览器验收390/1280px，覆盖屏幕外/中文不提交、滚动后才译、原节点保留、一个在途请求、隐藏后无下一条、关闭开关、缓存重开及失败不自动重试；合成API外网络拒绝，真实付费调用0。证据位于Git外/tmp/instant-ai-title-translation-20261123。

## 发布边界

当前公网仍200/0.34.1/auth_required=true，未执行任何发布器或重启。没有真实调用图片中的密钥、模型权限测试、后台预译、ASR、文章下载或正式库读写；标题数据仅在隔离夹具中使用。模型权限、真实译文质量及手机实际使用要在明确发布后首次前台调用核验。已提交请求可能完成的边界必须保留，不承诺关闭页面能撤销一次模型计费。
