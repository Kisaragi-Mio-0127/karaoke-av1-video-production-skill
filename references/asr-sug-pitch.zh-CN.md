# ASR、SUG与变调兼容性

[English](asr-sug-pitch.md) | 简体中文

## SUG版本门禁

- 从`src/strange_uta_game/__version__.py`读取应用版本，从`SugMigrator.CURRENT_VERSION`读取存储格式。不要使用过时的打包版本或README徽章作为解析器契约。
- 当前经过测试的基线是StrangeUtaGame1.4.5和SUG格式0.3.0。较新的应用可能仍保持相同的存储格式。
- 对于普通非MMS的SUG兼容性检查，使用目标仓库项目本地的Python运行`scripts/check_sug_compatibility.py`。加载是只读的；要求解析器/Schema兼容性，并执行针对性的渲染测试。日文MMS不使用前后哈希作为检查或门禁；任何`*_sha256`只记录在报告中。
- 未知SUG版本不会仅因JSON结构看起来相似就自动兼容。发布前必须进行真实的解析器加载和针对性渲染测试。

## 独立ASR与显式日文MMS策略

- `stable-ts`接收已知歌词或token作为强制对齐证据。独立ASR不带歌词提示进行转写，是单独的可选审核链路。
- 对新日文曲目，第一条命令使用`run_karaoke_japanese_full_auto.py`。将`run_karaoke_japanese_mms_workflow.py`作为分阶段恢复入口；它接受显式的单曲`--sug`，并在新输出内生成当前构图。
- 日文MMS入口通过`--mms-model-path`使用项目自有的`models/mms/model.pt`。`.cache`用于派生运行时数据和证据，不是模型权威来源；不存在模型下载回退。保持封面访问独立，并记录解析后的模型和缓存来源。
- 新输出目录下工作流子目录只能有`audit/`、`build/`和`render/`。通过audit和override-build门禁，并且仅允许将`build/timing_overrides.json`中已审核的`visual_release_overrides_ms`传入render。
- 批量运行绝不调用MMS。当固定路径的`timing_overrides`产物存在时，批量流程消费其中已有的`visual_release_overrides_ms`并记录产物身份；渲染器不会验证MMS来源。在提升批量前验证该产物和日文工作流门禁。
- 绝不能把确定性插值称为ASR回退。独立ASR无法运行时，记录`unresolved`及工具/模型错误；不要合成识别token、置信度或通过结论。
- 允许ASR提供支持、否决或保持未解析状态。它不得改写冻结歌词或直接选择时间戳。
- 明确记录按profile区分的归一化，并在Unicode归一化后保留源文本和读音。没有经过验证的适配器，不要推断支持另一种语言。

## 变调集成

- 用户请求变调时，在时间处理和渲染前运行`scripts/pitch_shift_audio.py`。
- 使用带符号的半音数、Rubber Band R3 Finer（`-3`），默认对人声材料启用共振峰保留（`-F`），速度比为1.0。
- 要求探测到的源编码为FLAC或PCM；拒绝MP3、AAC和其他有损输入，防止有损源被重新标记为无损。解码为float WAV，削波时使用额外余量重试，编码经过验证的FLAC/WAV，并将音频与JSON报告一起发布。
- 使用经过验证的变调FLAC作为对齐、预览和默认MP4 AAC-LC 320 kb/s的选定混音后源。只有显式选择`--lossless-companion`/`--lossless-output`并通过FLAC/PCM源探测后，才添加MKV FLAC伴侣文件；绝不从MP4 AAC流制作FLAC，并拒绝MP3/AAC请求。
- 变调输出只继承安全的描述性标签。FLAC保留内嵌封面，WAV写入对应的描述性标签，不复制技术元数据。
