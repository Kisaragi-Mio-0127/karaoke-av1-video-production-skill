# ASR, SUG, And Pitch Compatibility

[简体中文](asr-sug-pitch.zh-CN.md) | English

## SUG version gate

- Read the application version from `src/strange_uta_game/__version__.py` and the storage format from `SugMigrator.CURRENT_VERSION`. Do not use a stale packaging version or README badge as the parser contract.
- The current tested baseline is StrangeUtaGame 1.4.5 and SUG format 0.3.0. A newer application may keep the same storage format.
- Run `scripts/check_sug_compatibility.py` with the target repository's project-local Python for normal non-MMS SUG compatibility checks. Use the configured language profile for the documented ASR/alignment path. Loading is read-only; require parser/schema compatibility and a focused render test. Dedicated Japanese and zh/en MMS entries do not use before/after hashes in checks or gates; record any `*_sha256` only in reports.
- Unknown SUG versions are not automatically compatible merely because their JSON shape looks similar. Require a real parser load and a focused render test before release.

## Independent ASR and explicit Japanese MMS policy

- stable-ts receives known lyrics or tokens as forced-alignment evidence. Independent ASR transcribes without lyric prompts and is a separate optional review lane.
- `run_karaoke_japanese_mms_workflow.py` is the only documented MMS entry and is Japanese-only. It requires `--manifest`, `--song-id`, and a new `--output-dir`; the current composition is generated inside that output, while `--composition` remains an advanced gated override. Manifest/project defaults resolve the reviewed SUG, frozen lyrics, and MSST `Vocals.wav`, with optional `--source` and `--vocals-root` overrides. It accepts no separate SUG-path argument and is not a `zh`/`en` adapter or default ASR/MMS review.
- The Japanese MMS entry uses the project-owned `models/mms/model.pt` through `--mms-model-path`. `.cache` is for derived runtime data and evidence, not model authority; there is no model-download fallback. Keep cover access separate and record resolved model/cache provenance.
- Require only `audit/`, `build/`, and `render/` as workflow subdirectories under the new output directory. Pass audit and override-build gates, and allow only reviewed `visual_release_overrides_ms` from `build/timing_overrides.json` into render.
- A batch run never invokes MMS. When the fixed-path `timing_overrides` artifact exists, batch consumes its existing `visual_release_overrides_ms` and records the artifact identity; the renderer does not validate MMS provenance. Validate the artifact and the Japanese workflow gate before batch promotion.
- Never call deterministic interpolation an ASR fallback. When independent ASR cannot run, record `unresolved` with the tool/model error; do not synthesize recognized tokens, confidence, or a passing disposition.
- Allow ASR to support, veto, or remain unresolved. It must not rewrite frozen lyrics or directly choose timestamps.
- Keep profile-specific normalization explicit and preserve source text and readings after Unicode normalization. Do not infer support for another language without a validated adapter.

## Pitch integration

- Run `scripts/pitch_shift_audio.py` before timing and rendering when a key change is requested.
- Use signed semitones, Rubber Band R3 Finer (`-3`), formant preservation (`-F`) by default for vocal material, and tempo ratio 1.0.
- Require the probed source codec to be FLAC or PCM; reject MP3, AAC, and other lossy inputs so a lossy source cannot be relabeled as lossless. Decode to float WAV, retry with additional headroom on clipping, encode a verified FLAC/WAV, and publish the audio plus JSON report together.
- Use the verified shifted FLAC as the selected post-mix source for alignment, previews, and default MP4 AAC-LC 320 kb/s. Add an MKV FLAC companion only after explicit `--lossless-companion`/`--lossless-output` opt-in and a FLAC/PCM source probe; never make FLAC from the MP4 AAC stream, and reject MP3/AAC requests.
