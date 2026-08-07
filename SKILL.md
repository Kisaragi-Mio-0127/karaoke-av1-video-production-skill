---
name: karaoke-av1-video-production
description: Produce, review, re-encode, package, or debug Japanese, Chinese, and English karaoke videos and editable SUG timing projects with local audio, lyrics, MMS/ASR evidence, subtitle validation, AV1 4:2:0 release checks, and MP4-first delivery. Use for the normal route, the explicit Japanese MMS route, or the dedicated zh/en MMS route. Do not use for TTS, voice cloning, music generation, vocal separation, or standalone lyric transcription.
---

# Karaoke AV1 Video Production

Use this Skill to take an authorized local karaoke source through inspection,
timing evidence, preview, render, media verification, and rollback-safe
promotion. Keep the canonical editable project, derived timing evidence,
subtitle sources, and delivery media separate.

Read these references when needed:

- [MMS, model/cache, SUG, and route contract](references/mms-workflows.md)
- [single-source wide-layout contract](references/wide-visual-templates.md)
- [AV1/ffprobe command guidance](references/av1-420-commands.md)
- [subtitle, ruby, and editable-project gates](references/subtitle-timing-quality.md)
- [ASR, SUG compatibility, and pitch handling](references/asr-sug-pitch.md)
- [batch release gates](references/batch-release-gates.md)

## Route the request

Select one route before processing inputs. Do not add MMS flags to a different
entry point.

- **Normal Japanese:** run `scripts/run_karaoke_japanese_workflow.py`. It uses
  the Japanese profile and never runs MMS.
- **Normal Chinese/English:** run
  `scripts/run_karaoke_zh_en_workflow.py --language zh|en`. It enforces the
  no-ruby language profile and never runs MMS.
- **Japanese MMS:** run the dedicated
  `scripts/run_karaoke_japanese_mms_workflow.py`. It is Japanese-only and
  executes an MMS audit, build, companion-SUG step, and render gate. Use
  `--quality-policy strict|auto-fallback`; `strict` is the default. With
  `auto-fallback`, apply high-confidence MMS timing, retain canonical timing
  for low-confidence units, and do not require manual review to complete the
  automated run. Keep quality-gate and `unresolved` evidence unchanged; do
  not relax structural, subtitle, or media hard gates. A successful fallback
  reports `status: rendered-with-fallback` and exits 0, meaning only that the
  automated workflow completed.
- **Chinese/English MMS:** run the dedicated
  `scripts/run_karaoke_zh_en_mms_workflow.py --language zh|en`. It supports
  `--audit-only` and full mode; it must not be used for Japanese.
- **AV1 batch:** run `scripts/render_karaoke_direct_av1_420_album.py`. It
  never invokes MMS and consumes only an already reviewed timing sidecar.

Normal Japanese entry:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style spectrum
```

Normal Chinese/English entry:

```powershell
uv run --no-sync python scripts/run_karaoke_zh_en_workflow.py `
  --language <zh-or-en> --sug <project.sug> --audio <post-mix-audio> `
  --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style spectrum
```

Use a new, non-existent output directory for every one-click run. The wrapper
builds the current composition inside that directory. Pass `--cover` only when
the selected audio has no usable embedded cover, and `--background` only for
an explicit background choice. `vinyl` generates a fresh record asset in the
same run; `spectrum` creates and reports no vinyl. `--composition` and
`--vinyl` are advanced compatibility overrides, not normal inputs.

## MMS modes and artifacts

Use a new private output root inside the project and outside deliverables.
Keep the stage order explicit:

```text
audit -> recognition gate when required -> build -> companion SUG -> render
```

`audit-only` is a zh/en wrapper mode. It may omit ASR, writes local audit
evidence and a workflow report, and stops before build and render. It creates
no companion SUG and cannot authorize a release. The Japanese wrapper has no
`--audit-only` flag; use `scripts/audit_karaoke_mms_alignment.py` for a
Japanese evidence-only audit.

Full MMS build creates the companion before release decisions. In strict mode
(and on the zh/en route), unresolved evidence, a quality gate, or an ASR veto
retains it for manual timing review, skips render, and exits non-zero; Japanese
`auto-fallback` may render a structurally valid companion without manual review
while preserving those evidence results and all hard gates. Missing
`visual_release_overrides_ms` is not a separate failure: render still uses the
companion without a timing sidecar and its preserved canonical
`sentence_end_ts` release. The companion remains available for optional later
manual timing adjustment and never overwrites the canonical SUG.

Use the complete commands and stage-specific rules in
[mms-workflows.md](references/mms-workflows.md).

## Model and cache boundary

Use the project-owned model roots:

- `models/mms/model.pt` for MMS forced alignment.
- `models/whisper/<model>.pt`, or `models/whisper` through
  `--model-cache`, for independent ASR.

Use `--mms-model-path` or `--model-path` for an explicit local checkpoint. Do
not treat `.cache` as a model authority and do not enable download fallback.
Use `.cache/msst-vocals`, `.cache/asr-recognition`, and other task-owned
`.cache` locations only for derived runtime data and evidence. Legacy
`.cache/torch` or `.cache/whisper` weights do not satisfy the canonical model
contract.

Record resolved model identity and cache provenance in reports while redacting
unnecessary absolute paths. Do not copy private model weights or MMS evidence
into deliverables.

## MMS identity and gate boundary

The dedicated Japanese and zh/en MMS entries do not check hashes. Any
`*_sha256` field is report-only metadata and must not affect checks, gates,
exception handling, or exit status; use the resolved absolute-path, schema,
song/language, token/index, timeline, and (for zh/en) dual-ASR semantics in
the shared MMS reference. Existing normal non-MMS ASS/encoding checks remain
unchanged and are outside this rule.

## Language contract

- **Japanese (`ja`):** ruby may be present only in the reviewed canonical SUG.
  Pure katakana spans receive no separate ruby; a stale pure-katakana ruby is
  ignored by validation and rendering without mutating the source project.
  Preserve other reviewed ruby, validate lexical boundaries and rendered
  agreement, and reject any cross-singer ruby span. Pass canonical reviewed
  readings to MMS first and use generic reading fallback only for uncovered
  visible text. Numeric and mixed text use the same generic rules; keep
  release-specific corrections in external review data, never shared code.
- **Chinese (`zh`):** use whole-sentence context plus tone-less pinyin only as
  supplied alignment input. Preserve the frozen display text.
- **English (`en`):** use orthographic whole words as supplied alignment input.
  Keep one editable timing unit per word; renderer-only letter interpolation
  must not be persisted to the SUG.
- **Chinese and English display:** render zero ruby, zero pinyin, and zero
  phonetic overlays. MMS and stable-ts are supplied-token forced alignment,
  not independent ASR or phoneme recognition.
- **Independent ASR:** transcribe without lyric prompting, remain separate from
  forced alignment, and never rewrite frozen lyrics. Full zh/en MMS requires
  exactly two accepted reports: one `stem` lane and one `mix` lane, both with
  `support`.

Resolve singer identity only from explicit SUG metadata. Build one shared
`karaoke-color-plan/v1`; color planning must not mutate the source SUG.

## Editable-project and release rules

Treat the reviewed SUG as the editable source, not as an incidental export.
Use the project-local audio probe for manual review, prove that audio reached
the playback engine, and never auto-save a guarded probe session. Rebuild ASS
and media from a deliberately saved canonical source.

Keep the default output MP4 with hard subtitles, AV1 video, and AAC-LC audio.
Create an MKV/FLAC companion only after explicit `--lossless-companion` and a
probe proves a lossless FLAC or PCM source. Never transcode MP4 AAC to a
lossless companion or accept MP3/AAC as lossless.

## AV1 4:2:0 release gate

- Target the documented AV1 Main/`av01` profile with `yuv420p`, BT.709 metadata,
  and the project release frame rate/resolution. Verify the final stream with
  `ffprobe`; never infer 4:2:0 from the encoder list.
- Prefer the tested NVENC release lane after a real probe encode; use
  `libaom-av1` as the CPU/reproducible fallback. Keep speed claims separate
  from quality claims.
- Keep MP4/AAC as the default compatibility delivery. Require explicit
  opt-in and lossless-source proof for MKV/FLAC.
- Treat full null decode as an optional diagnostic. Record
  `performed: false` and a reason when it is not run; never turn an unperformed
  decode into a pass or a release blocker.
- Inspect representative frames, subtitle boundaries, audio/video sync, stream
  presence, timestamps, dimensions, and duration before promotion. Preserve a
  same-volume rollback copy and re-probe the promoted destination.

## First pass and reporting

1. Read project instructions and inventory audio, lyrics, SUG/ASS, artwork,
   fonts, model identities, and rights evidence.
2. Use `uv run --no-sync` with the existing project environment. Use a
   task-owned temporary output and preserve source artifacts until all gates
   pass.
3. Generate a short preview covering title, first lyric, longest line, dense
   timing, representative color/secondary states, and ending.
4. Report sanitized inputs, route and mode, source/model/cache identities,
   SUG and sidecar identities, timing/ASR decisions, AV1/audio/container
   choices, probe results, optional diagnostics, output paths, remaining risks,
   and the rollback point. Do not expose absolute local paths or unapproved
   media tags.

For any layout coordinate, font size, margin, or spectrum-bar question, read
[wide-visual-templates.md](references/wide-visual-templates.md). Do not add a
second numeric layout source.
