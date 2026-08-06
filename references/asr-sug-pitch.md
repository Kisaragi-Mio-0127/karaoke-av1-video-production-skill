# ASR, SUG, And Pitch Compatibility

[简体中文](asr-sug-pitch.zh-CN.md) | English

## SUG version gate

- Read the application version from `src/strange_uta_game/__version__.py` and the storage format from `SugMigrator.CURRENT_VERSION`. Do not use a stale packaging version or README badge as the parser contract.
- The current tested baseline is StrangeUtaGame 1.4.5 and SUG format 0.3.0. A newer application may keep the same storage format.
- Run `scripts/check_sug_compatibility.py` with the target repository's project-local Python. Each ASR/alignment run must select exactly one language, `ja`, `zh`, or `en`; test representative projects for those languages when available. Loading is read-only; require identical before/after hashes.
- Unknown SUG versions are not automatically compatible merely because their JSON shape looks similar. Require a real parser load and a focused render test before release.

## Independent ASR policy

- stable-ts and MMS_FA receive known lyrics or tokens and are forced-alignment evidence. Independent ASR transcribes without lyric prompts and is a separate review lane.
- Never call deterministic interpolation an ASR fallback. When independent ASR cannot run, record `unresolved` with the tool/model error; do not synthesize recognized tokens, confidence, or a passing disposition.
- Allow ASR to support, veto, or remain unresolved. It must not rewrite frozen lyrics or directly choose timestamps.
- Keep language-specific normalization explicit. Each run selects exactly one of `ja`, `zh`, or `en`. Simplified/traditional conversion is only a comparison normalization within `zh`, never a language fallback. Japanese must preserve kanji and kana exactly after Unicode normalization. English compares word tokens without inserting spaces into CJK text.

## Pitch integration

- Run `scripts/pitch_shift_audio.py` before timing and rendering when a key change is requested.
- Use signed semitones, Rubber Band R3 Finer (`-3`), formant preservation (`-F`) by default for vocal material, and tempo ratio 1.0.
- Require the probed source codec to be FLAC or PCM; reject MP3, AAC, and other lossy inputs so a lossy source cannot be relabeled as lossless. Decode to float WAV, retry with additional headroom on clipping, encode a verified FLAC/WAV, and publish the audio plus JSON report together.
- Use the verified shifted FLAC as the selected post-mix source for alignment, previews, MP4 AAC-LC 320 kb/s, and the MKV FLAC companion. Never make FLAC from the MP4 AAC stream.

## 中文说明

独立ASR不属于强制对齐失败后的后备流程。stable-ts和MMS_FA使用已知歌词或音素进行强制对齐；独立ASR不接收歌词提示，是独立复核证据。无法运行时必须记录`unresolved`及工具或模型错误，不能伪造识别词、置信度或通过结论。
