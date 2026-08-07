# Subtitle Timing And Quality Gates

[简体中文](subtitle-timing-quality.zh-CN.md) | English

Use these gates for semantic phrase segmentation, cue display, Japanese ruby, explicit multi-singer identity, secondary overlays, MMS timing evidence, and adjacent-line highlight release. Read [singer-overlays.md](singer-overlays.md) for the complete singer and top-overlay contract.

## Fact chain and semantic phrases

Track every generation through:

```text
source lyrics -> candidate ruby fill -> canonical SUG
-> contextual ruby review/correction -> timing and phrase decisions
-> read-only renderer -> ASS and render report
-> encoded video -> promoted or archived output
```

Record a hash or equivalent generation ID at every layer. The canonical SUG is the editable source of truth: candidate generation fills missing ruby, review writes accepted corrections, and rendering reads the reviewed project. The reviewed phrases must recompose each normalized source line exactly. Identify overrides by complete source line plus stable segment or occurrence identity, assert their hit counts, and reject unreachable, duplicate, or overly broad rules.

Record long but semantically complete phrases as reviewed exceptions and verify visual fit at the target font and size. Add semantic spacing only at approved breath or semantic boundaries, recording the character index and one pixel or em increment.

## Ruby and editable projects

Treat ruby word boundaries and reading correctness as separate gates. Preserve existing reviewed ruby, keep multi-kanji lexical units together when appropriate, and do not merge adjacent words merely because their readings are contiguous. Resolve context-sensitive readings from the complete phrase rather than a global surface-form replacement.

For every ruby-bearing line, inspect the canonical SUG `linked_to_next` chain and compare the same surface span and reading in the SUG, ASS/report, and rendered geometry. Record source, editable, and rendered status plus confidence, evidence, review identity, before/after SUG identities, exceptions, unchanged timing status, and representative frames.

Resolve each ruby span's singer before rendering. Use character-level `singer_id`, then sentence-level `singer_id`, then the explicit project default; reject a span whose linked surface characters resolve to different singers. Do not split or reassign it in the renderer.

Skip manual timing review when automatic evidence and release checks agree. When listening or editing is required, identify the canonical project, inspect recovery copies, resolve relative media paths from the project directory, and prove that audio reached the playback engine. The probe opens the project without saving; timing edits are made later in normal editable mode and saved to the canonical SUG.

## Editor, MMS, and independent ASR

MMS and separated-vocal results are timing evidence rather than delivery tracks. Record tool, model, version, input channel, generation identity, onset evidence, release evidence, and any A/B decision.

Independent ASR is separate from forced alignment. The documented route uses the configured Japanese profile; another profile requires a validated adapter. When independent ASR is unavailable, fails, or cannot match the frozen lyric window, record `unresolved` instead of substituting interpolation or forced-alignment output.

The editor probe isolates settings, cache, and recovery state; blocks saves; records project and audio identities, resolved media paths, engine metadata, finite non-empty waveform evidence, dirty state, and post-exit project identity. A forced exit or missing final identity check remains unresolved.

## Highlight, visual fit, and tests

Derive adjacent-line release from ASS karaoke tags, event timing, post-roll, and fade. Record event start, cumulative karaoke duration, event end, post-roll end, fade end, next-line visible start, and final release. Inspect boundary frames when evidence conflicts.

For per-character highlighting, record acoustic onset, visual onset, visual release, line release, event end, and fade end separately. Ensure every visible character has a strictly increasing visual start after ASS time quantization, including long marks, small kana, digits, and punctuation.

For cover-derived colors, record the extraction method, candidates, accepted RGB value, and review decision. Apply the resolved singer colour consistently to the editable singer colour, ASS `Main`/`Glow`, active cue, and any top secondary subtitle layers; keep inactive text white, verify RGB-to-ASS-BGR conversion, and inspect frames before, during, and after highlight.

Run renderer, preview, segmentation, cue, ruby, release, and packaging tests in a writable project-local temporary directory. Cover override reachability, phrase recomposition, cue timing, font-size exceptions, three-layer ruby status, editable-project identity, relative media resolution, audio-load evidence, MMS source identity, ASS/report identity, release fields, and boundary frames.
