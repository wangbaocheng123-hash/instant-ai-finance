# 模型先生顺序音频与中文日期播报复用核验

- 日期：2026-11-13
- 对应任务：P2-MODEL-36；决策：ADR-0087；候选版本：0.31.0。
- 范围：复用已保存视频的音轨，按现有作品序号向新作品继续，每条先报发布日期和序号。不重新下载或识别，不读取原文/评论，不调用付费语音服务。

## 复用结论与源码证据

| 能力 | 来源、版本与许可证 | 已核验的源码及使用位置 |
|---|---|---|
| 原音轨与有限时长 | 本仓库 `product/instant_ai/model_mr_audio.py`，ADR-0084；复用已存在的 ffmpeg/ffprobe | `prepare_model_mr_audio` 提取与缓存，`_probe` 校验音轨与时长，`_run` 限时并隐藏工具错误中的私有路径。新模块不重复实现原视频提取 |
| 浏览器 HLS 后备 | [video-dev/hls.js](https://github.com/video-dev/hls.js/tree/142fcab1fd0de99beafb0e1b07102ec674a9ddf9)，1.7.4，提交 `142fcab1fd0de99beafb0e1b07102ec674a9ddf9`，Apache-2.0 | `src/hls.ts` 的 `loadSource/attachMedia`；`src/is-supported.ts` 的 `isMSESupported`；`src/demux/tsdemuxer.ts`、`src/remux/mp4-remuxer.ts` 处理 AAC/MP3 TS。按需导入 `hls.js/light`，关闭 Worker，复用一个原生 audio。精确版本及包完整性保留在 package-lock |
| 小型离线中文语音运行库 | [thewh1teagle/espeakng-loader](https://github.com/thewh1teagle/espeakng-loader/tree/146599e29be31bf17d99f0bcb7dbb2f92aef3d95)，0.2.4，提交 `146599e29be31bf17d99f0bcb7dbb2f92aef3d95`；包装层 MIT | `src/espeakng_loader/__init__.py` 的 `get_library_path/get_data_path` 和 `build.sh` 确认二进制、数据目录及构建来源。只使用 wheel 中固定二进制/数据，不执行包装层 Python、不运行 pip 或系统安装 |
| 实际语音引擎 | [espeak-ng/espeak-ng](https://github.com/espeak-ng/espeak-ng/tree/4870adfa25b1a32b4361592f1be8a40337c58d6c)，上述载体的 espeak-ng 子模块提交 `4870adfa25b1a32b4361592f1be8a40337c58d6c`，GPL-3.0 | `src/include/espeak-ng/speak_lib.h` 的 `AUDIO_OUTPUT_SYNCHRONOUS`、`espeak_Initialize/SetSynthCallback/SetVoiceByName/Synth/Synchronize`；`espeak-ng-data/lang/sit/cmn`。通过 ctypes 在独立短进程生成 22050Hz PCM/WAV，不调用系统音频设备 |

不能把载体的 MIT 当成 eSpeak NG 二进制许可证。引擎完整 GPL 文本保存在 `product/instant_ai/licenses/espeak-ng-GPL-3.0.txt`，安装时附带 COPYING 与对应源码链接。hls.js 的 Apache 版权声明及完整许可证保存在前端 `/hls-js-license.txt`，NOTICE 记录精确来源。仍按单主人私人使用；未来分发须重新核对许可证义务。

## 协议与运行验证

- Apple 官方 [EVENT 播放列表](https://developer.apple.com/documentation/http-live-streaming/event-playlist-construction)规定列表追加且保留旧片段；[HLS 设备规范](https://developer.apple.com/streaming/hls-authoring-specification-for-apple-devices.html)作为原生播放参照。服务端按需追加 EVENT 清单，完成或达到定时后追加 ENDLIST；中文提示和原声都是真实媒体片段，不依赖页面 `ended` 或 speechSynthesis 回调跨条。
- iPhone 走原生 HLS。其他浏览器按需加载 Light 构建，通过 MSE 播放；支持 AAC 时使用 AAC，测试 Chromium 缺 AAC 时使用 MP3。`isSupported` 的广泛编解码探测不能代表测试环境的 MP3-only MSE，实际使用 `isMSESupported` 并检查媒体能力。所有库与分段仅同源读取，CSP 仅为 MSE 增加 `media-src blob:`。
- PyPI 的 Linux x86_64 wheel 下载为 10,078,484 字节，解包约 20,397,629 字节；固定 SHA-256：`08721baf27d13d461f6be6eed9a65277e70d68234ff484fd8b9897b222cdcb6d`。来源 URL 固定在 `model_mr_speech.py`。现场实际下载、哈希校验、私有解包与中文合成通过，生成 5.309 秒非静音 WAV；第二次禁止网络仍复用成功。未安装大型模型、全局环境或系统服务。
- 下载上限12MiB、解包32MiB，下载限时、目录拒绝符号链接/路径穿越；语音进程15秒超时，输出时长最多20秒。仅首次需要下载公共运行库；日期和作品信息不发送给外部服务。组件仅在现有 Linux x86_64 云端启用，其他服务器平台明确报组件不可用，原单曲模式可继续使用。
- 单元测试覆盖中文日期/序号与北京时间、未知日期、运行库校验/复用/恶意路径、按全库序号前进并跳过图文、AAC/MP3追加清单、有限900秒与当前进度、语音失败不悄悄略过、先认证后准备及私有 Range/HEAD/状态白名单。
- 实际中文语音与三条合成音频在390/1280px Chromium通过23项断言，跨条为内部10→30→400、全库序号1→3→4；独立4项原生分支契约校验同步设置音源及用户手势，但该分支的 play 被测试替身替代，不能称为 iPhone 真实播放。AAC 清单另由 ffmpeg 在没有页面脚本的条件下连续读取三条并解码。
- 真实 iPhone 主屏入口、锁屏跨过日期提示与多条作品、系统媒体按钮及定时停止须发布后独立验收。自动化证据不替代该验收。

## 数据与资源边界

派生分段在模型先生 Git 外 `audio-cache/sequences`，独立上限512MiB、单件128MiB、30天按需淘汰；既有单曲缓存512MiB保持，两类合计约1GiB，另有约20MiB一次性语音运行库。语音运行库在同资料根 `audio-tools/`，没有新数据库、外部记忆写入或报告回填。测试媒体、运行库、截图与临时清单均在 `/tmp`，不进入 Git。
