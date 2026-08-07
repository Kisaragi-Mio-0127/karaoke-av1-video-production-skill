# ASR、SUG与变调兼容性

[English](asr-sug-pitch.md) | 简体中文

## SUG版本门禁

- 从`src/strange_uta_game/__version__.py`读取应用版本，从`SugMigrator.CURRENT_VERSION`读取存储格式；不要把旧打包版本或README徽章当作解析器契约。
- 当前已验证基线是StrangeUtaGame 1.4.5和SUG格式0.3.0。应用可以升级而保持相同存储格式。
- 使用目标仓库项目本地Python运行`scripts/check_sug_compatibility.py`。文档化的ASR/对齐流程使用已配置的语言 profile。加载只读，前后哈希必须一致。
- 未知SUG版本即使JSON结构相似也不能自动兼容，正式发布前必须通过真实解析器加载和针对性渲染测试。

## 独立ASR规则

- `stable-ts`和`MMS_FA`接收已知歌词或音素，属于强制对齐证据。`audit_karaoke_mms_alignment.py`是显式MMS审计；`build_karaoke_mms_overrides.py`单独把接受的visual-release覆盖冻结到`timing_overrides.json`。默认一键入口没有MMS参数，永远不会生成、消费或校验MMS；正式AV1 4:2:0批量渲染同样不会运行MMS，只会自动消费已有的`<album-root>/sources/timing_overrides.json`。
- 已安装的`run_karaoke_japanese_mms_workflow.py`入口要求已有manifest、规范SUG、冻结歌词和项目本地MSST Vocals，并在全新的、非deliverables暂存输出中按`audit -> build -> render`运行。审计门禁必须先通过；构建和渲染必须为这些输入及MMS访问策略携带匹配的来源记录。
- 在MMS构建产物中，只有`visual_release_overrides_ms`进入渲染输入并可以影响ASS/视频。`character_overrides_ms`只保留为证据和来源记录，不应用到SUG、ASS时间或编码视频。
- MMS模型访问默认离线：提供`--mms-model-path <local-mms-model>`，或显式使用`--allow-mms-network`。封面访问独立控制，只有传入`--allow-cover-network`才允许联网。
- 不能把确定性插值称为ASR后备方案。独立ASR无法运行时记录`unresolved`和工具/模型错误，不能伪造识别词、置信度或通过结论。
- ASR只能支持、否决或保持未解决，不能改写冻结歌词，也不能直接决定最终时间戳。
- profile 专用的归一化必须显式处理；Unicode归一化后保留源文本和读音。不通过未经验证的 adapter 推断其他语言支持。

## 变调整合

- 请求升降调时，在时间轴和渲染前运行`scripts/pitch_shift_audio.py`。
- 使用带符号的半音数、Rubber Band R3 Finer（`-3`），含人声时默认启用共振峰保持（`-F`），速度比例保持1.0。
- 探测到的源编码必须是FLAC或PCM；拒绝MP3、AAC和其他有损输入，不能把有损来源重新标记为无损。先解码为浮点WAV；出现削波时增加余量重试，再编码并验证FLAC/WAV，音频和JSON报告作为一组发布。
- 验证后的变调FLAC用于对齐、预览和默认MP4 AAC-LC 320 kb/s。只有显式请求`--lossless-companion`/`--lossless-output`且探测源为FLAC/PCM时才加入MKV FLAC；绝不能从MP4 AAC反向生成FLAC，并且必须拒绝MP3/AAC请求。
