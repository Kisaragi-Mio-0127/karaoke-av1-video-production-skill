# Subtitle Timing And Quality Gates

[简体中文](subtitle-timing-quality.zh-CN.md) | English

Use these gates for semantic phrase segmentation, cue display, Japanese ruby, explicit multi-singer identity, secondary overlays, timing evidence from explicit MMS audit/override, and adjacent-line highlight release. Read [singer-overlays.md](singer-overlays.md) for the complete singer and top-overlay contract.

## Fact chain and semantic phrases

Track every generation through:

```text
source lyrics -> candidate ruby fill -> canonical SUG
-> contextual ruby review/correction -> timing and phrase decisions
-> read-only renderer -> ASS and render report
-> encoded video -> promoted or archived output
```

Record a hash or equivalent generation ID at every layer. The canonical SUG is the editable source of truth: candidate generation fills missing ruby, review writes accepted corrections, and rendering reads the reviewed project. The reviewed phrases must recompose each normalized source line exactly. Identify overrides by complete source line plus stable segment or occurrence identity, assert their hit counts, and reject unreachable, duplicate, or overly broad rules.

For the explicit MMS entry, keep the MMS lane in the same fact chain:

```text
manifest + canonical SUG + frozen lyrics + MSST Vocals
-> MMS audit (audit gate) -> override build (build gate)
-> visual-release projection -> read-only renderer -> ASS/report/video
```

The audit, build, and render outputs must carry matching input and generation
identities. Keep all of them in a new, non-deliverables staging output; do not
promote or overwrite a deliverable until the ordinary release gates pass.

Record long but semantically complete phrases as reviewed exceptions and verify visual fit at the target font and size. Add semantic spacing only at approved breath or semantic boundaries, recording the character index and one pixel or em increment.

## Ruby and editable projects

Treat ruby word boundaries and reading correctness as separate gates. Preserve existing reviewed ruby, keep multi-kanji lexical units together when appropriate, and do not merge adjacent words merely because their readings are contiguous. Resolve context-sensitive readings from the complete phrase rather than a global surface-form replacement.

For every ruby-bearing line, inspect the canonical SUG `linked_to_next` chain and compare the same surface span and reading in the SUG, ASS/report, and rendered geometry. Record source, editable, and rendered status plus confidence, evidence, review identity, before/after SUG identities, exceptions, unchanged timing status, and representative frames.

Resolve each ruby span's singer before rendering. Use character-level `singer_id`, then sentence-level `singer_id`, then the explicit project default; reject a span whose linked surface characters resolve to different singers. Do not split or reassign it in the renderer.

Skip manual timing review when automatic evidence and release checks agree. When listening or editing is required, identify the canonical project, inspect recovery copies, resolve relative media paths from the project directory, and prove that audio reached the playback engine. The probe opens the project without saving; timing edits are made later in normal editable mode and saved to the canonical SUG.

## Editor, MMS, and independent ASR

MMS and separated-vocal results are timing evidence rather than delivery tracks. Record tool, model, version, input channel, generation identity, onset evidence, release evidence, and any A/B decision. The default one-click and batch routes never generate, consume, or validate MMS. The installed `run_karaoke_japanese_mms_workflow.py` entry requires an existing manifest, canonical SUG, frozen lyrics, and project-local MSST Vocals before it starts. It must run `audit -> build -> render` in that order in a new, non-deliverables staging output.

The audit gate fails closed for missing, stale, mismatched, unresolved, or vetoed evidence. The build gate consumes only a passing audit and binds the manifest, SUG, frozen lyrics, MSST Vocals, MMS access policy, and audit identity into its provenance. Of the MMS build outputs, only `visual_release_overrides_ms` may enter the render input and affect the ASS/video. `character_overrides_ms` remains evidence and provenance and is not applied to the SUG, ASS timing, or encoded video. The render gate requires matching audit/build provenance and records the audit/build/render identities. `audit_karaoke_mms_alignment.py` and `build_karaoke_mms_overrides.py` remain explicit standalone tools; formal batch rendering does not run MMS.

MMS model access is offline by default. Provide `--mms-model-path <local-mms-model>` or explicitly authorize model network access with `--allow-mms-network`. Cover retrieval is an independent policy and remains offline unless `--allow-cover-network` is passed; neither flag authorizes the other lane.

If the formal AV1 4:2:0 batch finds the fixed path `<album-root>/sources/timing_overrides.json`, it consumes existing visual-release overrides only; it does not create the file or perform an MMS audit.

Independent ASR is separate from forced alignment. The documented route uses the configured Japanese profile; another profile requires a validated adapter. When independent ASR is unavailable, fails, or cannot match the frozen lyric window, record `unresolved` instead of substituting interpolation or forced-alignment output.

The editor probe isolates settings, cache, and recovery state; blocks saves; records project and audio identities, resolved media paths, engine metadata, finite non-empty waveform evidence, dirty state, and post-exit project identity. A forced exit or missing final identity check remains unresolved.

## Highlight, visual fit, and tests

Derive adjacent-line release from ASS karaoke tags, event timing, post-roll, and fade. Record event start, cumulative karaoke duration, event end, post-roll end, fade end, next-line visible start, and final release. Inspect boundary frames when evidence conflicts.

For per-character highlighting, record acoustic onset, visual onset, visual release, line release, event end, and fade end separately. Ensure every visible character has a strictly increasing visual start after ASS time quantization, including long marks, small kana, digits, and punctuation.

For cover-derived colors, record the extraction method, candidates, accepted RGB value, and review decision. The current extractor excludes near-black chroma noise and aggregates candidate pixel area across neighbouring colours in Lab space; do not promote rare JPEG noise into the primary colour. Apply the resolved singer colour consistently to the editable singer colour, ASS `Main`/`Glow`, active cue, and any top secondary subtitle layers; keep inactive text white, verify RGB-to-ASS-BGR conversion, and inspect frames before, during, and after highlight.

Run renderer, preview, segmentation, cue, ruby, release, and packaging tests in a writable project-local temporary directory. Cover override reachability, phrase recomposition, cue timing, font-size exceptions, three-layer ruby status, editable-project identity, relative media resolution, audio-load evidence, MMS audit/build/render gates and provenance when the explicit MMS entry is in scope, the distinction between `visual_release_overrides_ms` and `character_overrides_ms`, ASS/report identity, release fields, and boundary frames.
