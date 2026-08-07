# ASR, SUG, And Pitch Compatibility

[简体中文](asr-sug-pitch.zh-CN.md) | English

## SUG version gate

- Read the application version from `src/strange_uta_game/__version__.py` and the storage format from `SugMigrator.CURRENT_VERSION`. Do not use a stale packaging version or README badge as the parser contract.
- The current tested baseline is StrangeUtaGame 1.4.5 and SUG format 0.3.0. A newer application may keep the same storage format.
- Run `scripts/check_sug_compatibility.py` with the target repository's project-local Python. Use the configured language profile for the documented ASR/alignment path. Loading is read-only; require identical before/after hashes.
- Unknown SUG versions are not automatically compatible merely because their JSON shape looks similar. Require a real parser load and a focused render test before release.

## Independent ASR policy

- `stable-ts` and `MMS_FA` receive known lyrics or tokens and are forced-alignment evidence. `audit_karaoke_mms_alignment.py` is the explicit MMS audit; `build_karaoke_mms_overrides.py` separately freezes accepted visual-release overrides into `timing_overrides.json`. The default one-click route has no MMS parameters and never generates, consumes, or validates MMS; formal AV1 4:2:0 batch rendering also does not run MMS and only auto-consumes an existing `<album-root>/sources/timing_overrides.json`.
- The installed `run_karaoke_japanese_mms_workflow.py` entry requires an existing manifest, canonical SUG, frozen lyrics, and project-local MSST Vocals. It must run `audit -> build -> render` in a new, non-deliverables staging output. The audit gate must pass before build; build and render must carry matching provenance for those inputs and the MMS access policy.
- Of the MMS build outputs, only `visual_release_overrides_ms` enters the render input and can affect the ASS/video. `character_overrides_ms` remains evidence and provenance and is not applied to the SUG, ASS timing, or encoded video.
- MMS model access is offline by default: provide `--mms-model-path <local-mms-model>` or explicitly use `--allow-mms-network`. Cover access is independent and remains offline unless `--allow-cover-network` is passed.
- Never call deterministic interpolation an ASR fallback. When independent ASR cannot run, record `unresolved` with the tool/model error; do not synthesize recognized tokens, confidence, or a passing disposition.
- Allow ASR to support, veto, or remain unresolved. It must not rewrite frozen lyrics or directly choose timestamps.
- Keep profile-specific normalization explicit and preserve source text and readings after Unicode normalization. Do not infer support for another language without a validated adapter.

## Pitch integration

- Run `scripts/pitch_shift_audio.py` before timing and rendering when a key change is requested.
- Use signed semitones, Rubber Band R3 Finer (`-3`), formant preservation (`-F`) by default for vocal material, and tempo ratio 1.0.
- Require the probed source codec to be FLAC or PCM; reject MP3, AAC, and other lossy inputs so a lossy source cannot be relabeled as lossless. Decode to float WAV, retry with additional headroom on clipping, encode a verified FLAC/WAV, and publish the audio plus JSON report together.
- Use the verified shifted FLAC as the selected post-mix source for alignment, previews, and default MP4 AAC-LC 320 kb/s. Add an MKV FLAC companion only after explicit `--lossless-companion`/`--lossless-output` opt-in and a FLAC/PCM source probe; never make FLAC from the MP4 AAC stream, and reject MP3/AAC requests.
