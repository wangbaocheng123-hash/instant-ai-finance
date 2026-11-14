# 模型先生固定日期录音复用核验

- 日期：2026-11-15；对应P2-MODEL-37、ADR-0088、0.32.0候选。
- 用户明确指出只需日期、数字等少量字词，应固定音频改善颤音；本次不引入大型模型或在线付费语音调用。

## 来源与证据

| 项目 | 固定来源 | 已核验的路径/含义 |
|---|---|---|
| 中文语音素材 | [rts-cn/zh](https://github.com/rts-cn/zh/tree/bd238bccc671c784e0daed880d558d258a676e1a)，提交`bd238bccc671c784e0daed880d558d258a676e1a` | `cn/link/digits/8000`的0～19、整十、hundred/thousand和h-序数；`cn/link/time/8000`的year/month/day；`cn/link/directory/8000/dir-result_match.wav`的第一个“条” |
| 使用许可 | 同提交`LICENSE`及`README.md` | LICENSE为Mozilla Public License 2.0；README明确录音素材按MPL发布且再发布必须附README和LICENSE。完整副本放在`product/instant_ai/licenses/chinese-prompts-*`，运行词库同附，不混入许可说明不同的另一个语音包 |
| 固定清单 | 本仓库`announcement_prompts.json` | 53个原文件逐一SHA-256锁定，URL含完整提交；不是读取可变master或搜索结果。运行数据不进入Git |
| 播报脚本参照 | [FreeSWITCH中文提示词](https://github.com/signalwire/freeswitch/blob/master/docs/phrase/phrase_zh_CN.xml) | 已读取digits段的0、十、整十、第一/第二等h-序数、百、千及“百万”；directory段明确`dir-result_match.wav`为“条相应的结果”。该链接用于核对提示词语义，实际下载仍只使用上述已锁定素材仓库，不把这个可变脚本当音频身份 |
| 本地实现 | `model_mr_prompts.py`、`model_mr_speech.py` | `announcement_parts/_number`仅列出固定词；`ensure_recorded_prompts`按哈希下载/校验/私有缓存；`_pcm`一次性格式统一；`render_recorded_announcement`离线拼接；`synthesize_announcement`优先固定词，未知日期继续明确报未知 |

README记录来源有多人录音、编辑及部分TTS补全，不能宣称每个片段都是真人新录或同一录音棚高清原件。本次只按明确MPL许可复用固定素材，保留其来源与编辑说明。

## 已执行的验证

- 原件包含8kHz A-law和PCM WAV，直接使用Python wave读取A-law会失败，实际已改为复用现有ffmpeg解码，未安装新系统组件。派生以16kHz单声道PCM保存；固定峰值目标约-4dBFS、边缘5ms淡入淡出，不逐次改变音高或使用动态合成韵律。
- 对“条”源片段查看实际能量边界，首词后约0.34～0.47秒为静音，下一词约0.5秒开始；固定取0～0.42秒，附来源及编辑说明。其余词保留语音和短边缘静音，组间补短停顿。
- 同机实际下载53个锁定片段并完成私有缓存；包含说明文件的缓存共1,042,133字节。合成示例“二零二六年十一月十四日，第一百二十九条”长8.425秒、16000Hz、峰值20600/32768、满幅削波采样0。禁止网络后第二次合成仍成功，输出字节相同。试听仅示例日期，不推断第129条实际发布日期。
- 固定词组顺序覆盖年、月、日、129/21/101/1001等序号；来源脚本的million实际为“百万”，未误用为“万”。高位长编号可逐位报数。
- 自动检查只能证实身份、拼接顺序、时长、幅度、缓存及播放行为；清晰度的主观听感和手机扬声器效果仍以所有者试听/真机为准。自动测试不冒充听觉识别或iPhone锁屏验收。

## 边界

不发送作品日期、序号、原文、评论或凭据给外部服务；只下载公共固定文件。没有重新下载/识别视频、收费模型调用、数据库迁移、外部观点记忆或报告回填。旧eSpeak NG组件仅为“发布日期未知”等不匹配固定语法的明确文本保留原离线后备，不因此把未知日期补成当天。
