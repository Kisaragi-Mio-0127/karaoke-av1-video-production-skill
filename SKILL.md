---
name: karaoke-av1-video-production
description: Produce, rerender, package, or debug Japanese karaoke videos and editable SUG timing projects with local audio, frozen lyrics, MSST/MMS evidence, automatic layout generation, subtitle validation, and AV1 4:2:0 MP4 release checks. Use for a new Japanese full-auto run, a reviewed-SUG rerender, staged Japanese MMS recovery, or generic AV1 packaging. Do not use for TTS, voice cloning, music generation, vocal separation, or standalone lyric transcription.
---

# Karaoke AV1 Video Production

Use this Skill only for the public Japanese/general StrangeUtaGame integration.
Keep canonical inputs, private generated timing evidence, companion SUG files,
and delivery media separate. Do not add or route Chinese/English workflows
through this public Skill.

Read these references when needed:

- [full-auto and staged MMS contract](references/mms-workflows.md)
- [StrangeUtaGame installation and environment](references/strangeutagame-integration.md)
- [wide-layout contract](references/wide-visual-templates.md)
- [subtitle and editable-project gates](references/subtitle-timing-quality.md)
- [AV1/ffprobe guidance](references/av1-420-commands.md)
- [batch release gates](references/batch-release-gates.md)

## Runtime boundary

- Run production commands from the StrangeUtaGame root with its existing
  `.venv` through `uv run --no-sync`.
- The public runtime follows bootstrap hardware detection with
  `--device auto`. Override it explicitly with `--device cuda` or
  `--device cpu` when the target policy requires a fixed backend.
- Production commands use project-owned `models/mms/model.pt` and
  `models/whisper` and do not implicitly download models.
- Use the matched project tools under `tools/ffmpeg/bin`; install and verify
  them with the StrangeUtaGame integration reference.
- The public `check_karaoke_environment.py` does not actively initiate network
  requests. It checks model sizes by default and reads full model files only
  with `--deep-verify`; a custom manifest requires `--allow-custom-manifest`.
- Run `bootstrap_karaoke_environment.py` only as an explicit setup action. It
  probes NVIDIA/CPU, reuses or creates the single `target/.venv`, installs the
  version-pinned Python packages, and downloads missing MMS/Whisper files into
  `target/models/`. MMS download requires
  `--accept-mms-cc-by-nc-4-0`; a managed Python download requires
  `--allow-python-download`; a custom manifest requires
  `--allow-custom-manifest`. It does not manage git, uv, ffmpeg, ffprobe, or
  GPU drivers. See the integration reference for exact commands and boundaries.

## Default: Japanese full-auto

Run the Japanese full-auto entry from the StrangeUtaGame project root:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py `
  --manifest <manifest> --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --output-dir .render-work/<new-run-dir>
```

The output directory must be new. The command prepares one MSST vocal stem,
builds a private initial SUG, runs Japanese MMS, creates a separate editable
companion SUG, generates the current layout, renders AV1 MP4, and writes stage
reports. It defaults to `--quality-policy auto-fallback` and
`--visual-style spectrum`.

The MSST adapter is auto-discovered from supported local installations; an
explicit `KARAOKE_MSST_PREPARATION_SCRIPT` path overrides discovery.

The public runtime selection is `auto`, matching bootstrap's CUDA/CPU probe.
Pass `--device cuda` or `--device cpu` to pin the backend explicitly.

Treat `rendered-with-fallback` as successful automation with retained quality
evidence, not as a human quality approval. Low-confidence units retain their
initial timing. Manual or Agent adjustment of the companion SUG is optional.
Use `--quality-policy strict` only when quality uncertainty should stop before
render and hand the companion to review.

## Japanese SUG and ruby policy

When creating a new Japanese SUG, tokenize each lyric sentence as a whole
sentence, then use the project dictionary to generate phrase groups and
contextual ruby. Pure katakana receives no ruby. Ruby generation is fill-missing
only and must never overwrite existing reviewed ruby. Manual or Agent review of
the generated companion remains optional. MMS may adjust timing/alignment, but
must not rewrite ruby or frozen display text.

## Existing SUG rerender

Use the normal route when an existing or manually adjusted SUG should be
rendered again. It does not run MSST or MMS:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <existing-or-adjusted.sug> --audio <post-mix-audio> `
  --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style spectrum
```

The wrapper generates the current composition inside the output directory.
Choose `vinyl` when a new record asset is wanted; `spectrum` creates no vinyl.

## Staged MMS and recovery

Use `scripts/run_karaoke_japanese_mms_workflow.py` for stage-level recovery,
evidence inspection, or rerunning from an explicit private SUG. This lower
level route performs:

```text
audit -> build -> companion SUG -> render
```

It supports `--sug`, `--quality-policy strict|auto-fallback`, automatic layout
generation, and the same hard subtitle/media gates as full-auto. The original
SUG remains unchanged. See [mms-workflows.md](references/mms-workflows.md) for
the exact contract.

## Models, cache, and language

- Select MMS from `models/mms/model.pt` and alignment/recognition weights from
  project-owned `models/whisper`. Do not treat `.cache` as model storage.
- Keep decoded MSST inputs, vocal stems, runtime files, and derived evidence in
  task-owned `.cache` locations.
- Preserve reviewed Japanese ruby. Pure katakana receives no separate ruby;
  stale pure-katakana ruby is ignored without mutating the source SUG.
- Pronunciation validation remains optional. The staged/direct CLIs expose
  `--pronunciation-validation {off,optional,required}`; use `required` only
  when that gate is explicitly requested.
- Resolve singer colours from explicit SUG singer metadata and the shared
  ordered colour plan. Keep per-song decisions outside shared code.

## Release rules

- Generate the current wide layout from the shared source of truth; do not add
  a second set of layout constants.
- Deliver MP4 by default with AV1 Main-compatible `yuv420p` video, BT.709
  metadata, hard subtitles, and AAC-LC audio.
- Create MKV/FLAC only after explicit opt-in and proof of a lossless source.
- Treat full null decode as an optional diagnostic, not a default release gate.
- Verify stream presence, codec, pixel format, dimensions, frame rate,
  timestamps, duration, subtitles, representative frames, and promotion
  rollback before release.
- Use `uv run --no-sync` with the existing project environment. Do not create a
  new Python environment for each run.

Use `scripts/render_karaoke_direct_av1_420_album.py` only for batch rendering
from already reviewed timing; it does not generate timing or invoke MMS.
