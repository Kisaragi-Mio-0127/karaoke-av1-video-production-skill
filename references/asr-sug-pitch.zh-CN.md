# ASR、SUG与变调兼容性

[English](asr-sug-pitch.md) | 简体中文

## SUG版本门禁

- 从`src/strange_uta_game/__version__.py`读取应用版本，从`SugMigrator.CURRENT_VERSION`读取存储格式；不要把旧打包版本或README徽章当作解析器契约。
- 当前已验证基线是StrangeUtaGame 1.4.5和SUG格式0.3.0。应用可以升级而保持相同存储格式。
- 使用目标仓库项目本地Python运行`scripts/check_sug_compatibility.py`。每次ASR/对齐运行必须恰好选择`ja`、`zh`或`en`之一；条件允许时分别检查这些语言的代表性项目。加载只读，前后哈希必须一致。
- 未知SUG版本即使JSON结构相似也不能自动兼容，正式发布前必须通过真实解析器加载和针对性渲染测试。

## 独立ASR规则

- stable-ts和MMS_FA接收已知歌词或音素，属于强制对齐证据；独立ASR不接收歌词提示，是独立复核流程。
- 不能把确定性插值称为ASR后备方案。独立ASR无法运行时记录`unresolved`和工具/模型错误，不能伪造识别词、置信度或通过结论。
- ASR只能支持、否决或保持未解决，不能改写冻结歌词，也不能直接决定最终时间戳。
- 语言归一化必须显式区分，每次运行只选`ja`、`zh`或`en`之一。繁简转换只用于`zh`内部比较，不是语言后备；日语在Unicode归一化后仍保留汉字和假名；英文按词比对，不能给中日韩歌词插入空格。

## 变调整合

- 请求升降调时，在时间轴和渲染前运行`scripts/pitch_shift_audio.py`。
- 使用带符号的半音数、Rubber Band R3 Finer（`-3`），含人声时默认启用共振峰保持（`-F`），速度比例保持1.0。
- 探测到的源编码必须是FLAC或PCM；拒绝MP3、AAC和其他有损输入，不能把有损来源重新标记为无损。先解码为浮点WAV；出现削波时增加余量重试，再编码并验证FLAC/WAV，音频和JSON报告作为一组发布。
- 验证后的变调FLAC同时作为对齐、预览、MP4 AAC-LC 320 kb/s和MKV FLAC的音频源。绝不能从MP4 AAC反向生成FLAC。
