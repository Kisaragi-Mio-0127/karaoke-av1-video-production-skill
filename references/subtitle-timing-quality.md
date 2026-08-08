# Subtitle Timing And Quality Gates

[简体中文](subtitle-timing-quality.zh-CN.md) | English

Use these gates for semantic phrase segmentation, cue display, Japanese ruby,
the Japanese full-auto and staged MMS contracts, and
adjacent-line highlight release. Do not infer an MMS interface for another
language from this reference.

## Fact chain and semantic phrases

Track every generation through:

```text
source lyrics -> candidate ruby fill -> canonical SUG
-> optional contextual ruby review/correction -> timing and phrase decisions
-> read-only renderer -> ASS and render report
-> encoded video -> promoted or archived output
```

Record a generation ID or equivalent evidence at every layer. For dedicated
Japanese MMS, any `*_sha256` value is report-only and never a check,
gate, exception, or exit input. The canonical SUG is the editable source of
truth: candidate generation fills missing ruby, optional review may write accepted
corrections, and rendering reads the selected project. The display phrases
must recompose each normalized source line exactly. Identify overrides by
complete source line plus stable segment or occurrence identity, assert their
hit counts, and reject unreachable, duplicate, or overly broad rules.

Record long but semantically complete phrases as reviewed exceptions and verify visual fit at the target font and size. Add semantic spacing only at approved breath or semantic boundaries, recording the character index and one pixel or em increment.

For Japanese wide layout, treat whitespace inside one timed source line as an
explicit semantic or breathing boundary, not as a source-line break. When the
visible line exceeds the normal display-length target, group those whitespace
segments into balanced phrases before the measured-width fast path; merge
avoidable fragments shorter than the minimum phrase length. A compact line may
retain each source-space boundary as a subtle semantic gap, but the whitespace
character itself must never become a timed or highlighted glyph event. For a
source line without usable whitespace, measure the complete line before
applying the character-count fallback. Never split inside a continuous
katakana run or across a canonical `linked_to_next` word span. Short-run
rebalancing and explicit display overrides must preserve the same word-span
boundary. These are language
rules, not song-specific display overrides.

Sentence-level Japanese ruby analysis may normalize a run of repeated source
spaces internally, but the canonical SUG must restore the exact frozen
whitespace axis before applying word ruby. Align generated ruby by the visible
character sequence, keep every original whitespace character and timing field,
and never link a ruby span across source whitespace.

## Ruby and editable projects

Treat ruby word boundaries and reading correctness as separate gates. Pure
katakana needs no separate ruby. Ignore stale pure-katakana ruby spans in the
read-only canonical view without rewriting the source SUG. Preserve other
reviewed ruby, keep multi-kanji lexical units together when appropriate, and
do not merge adjacent words merely because their readings are contiguous.
Resolve context-sensitive readings from the complete phrase rather than a
global surface-form replacement.

For every ruby-bearing line, inspect the canonical SUG `linked_to_next` chain and compare the same surface span and reading in the SUG, ASS/report, and rendered geometry. Record source, editable, and rendered status plus confidence, evidence, review identity, before/after SUG identities, exceptions, unchanged timing status, and representative frames.

Skip manual timing review when automatic evidence and release checks agree. When listening or editing is required, identify the canonical project, inspect recovery copies, resolve relative media paths from the project directory, and prove that audio reached the playback engine. The probe opens the project without saving; timing edits are made later in normal editable mode and saved to the canonical SUG.

## Explicit Japanese MMS workflow, editor, and independent ASR

`run_karaoke_japanese_full_auto.py` is the default first-run entry, while
`run_karaoke_japanese_mms_workflow.py` is the staged MMS/recovery entry. It is
Japanese-only and separate from batch rendering. Full-auto requires
`--manifest`, `--song-id`, `--source`, and a new `--output-dir`; it prepares a
private initial SUG before invoking the staged wrapper. The staged wrapper may
instead receive a single explicit `--sug` for recovery or rerendering. Both
routes build the current composition in `render/artwork-current`, while an
explicit `--composition` remains an advanced gated override. Neither route
may refetch lyrics or mutate its resolved inputs.

The workflow uses the project-owned `models/mms/model.pt` checkpoint through
`--mms-model-path`. `.cache` is reserved for derived runtime data and evidence;
it is not a model authority and no download fallback is part of this contract.
Cover access remains a separate policy decision. Neither policy authorizes
remote lyrics, audio, or input replacement. Record the resolved model identity
and cache provenance without exposing unnecessary absolute paths.
Record the resolved model, both network decisions, and all input identities,
then resolve the selected inputs to absolute paths and run:

```text
absolute-path/schema/song-language/token-index/timeline preflight
-> MMS dual-audio audit -> timing-override build
-> render gate -> new ASS/report/video output
```

Absolute-path/schema/song-language, token/index, timeline, and
ASS/report/media structural semantics must pass before rendering. Under
`strict`, the automatic audit and override-build quality gates must also pass;
under `auto-fallback`, their uncertainty is recorded and usable initial timing
continues to rendering without human approval. The wrapper creates
`audit/`, `build/`, and `render/`; these are its only workflow subdirectories. Only
reviewed `visual_release_overrides_ms` from `build/timing_overrides.json` enter
`render/`. MMS audit data, other build values, and separated vocals remain
timing evidence, not delivery tracks. A failed structural gate must not create
or replace a release video. If the dedicated entry is absent, do not
recreate it by adding MMS flags to another workflow.

Independent ASR is a separate optional lane from forced alignment. The
documented route uses the configured Japanese profile; another profile requires
a validated adapter. When independent ASR is unavailable, fails, or cannot
match the frozen lyric window, record `unresolved` instead of substituting
interpolation or forced-alignment output.

Batch rendering never runs MMS. If the fixed-path `timing_overrides` artifact
exists, batch automatically consumes the existing `visual_release_overrides_ms` and
records the artifact identity; it neither creates overrides nor asks MMS to
generate them. The renderer does not validate MMS provenance, so validate the
artifact's source, generation identity, review status, and Japanese workflow
gate before the batch run.

The editor probe isolates settings, cache, and recovery state; blocks saves;
records project and audio identities, resolved media paths, engine metadata,
finite non-empty waveform evidence, dirty state, and post-exit project
identity. A forced exit or missing final identity check remains unresolved.

## Highlight, visual fit, and tests

Derive adjacent-line release from ASS karaoke tags, event timing, post-roll, and fade. Record event start, cumulative karaoke duration, event end, post-roll end, fade end, next-line visible start, and final release. Inspect boundary frames when evidence conflicts.

For per-character highlighting, record acoustic onset, visual onset, visual release, line release, event end, and fade end separately. Ensure every visible character has a strictly increasing visual start after ASS time quantization, including long marks, small kana, digits, and punctuation.

For cover-derived colors, filter near-black pixels by absolute chroma before Lab-neighbourhood area aggregation. Record the extraction method, extractor identity, ordered palette, candidates, accepted RGB value, and review decision. Apply the accepted color consistently to the editable singer color, ASS `Main`/`Glow`, and active cue color, verify RGB-to-ASS-BGR conversion, and inspect frames before, during, and after highlight.

Run renderer, preview, segmentation, cue, ruby, release, and packaging tests in a writable project-local temporary directory. Cover override reachability, phrase recomposition, cue timing, font-size exceptions, three-layer ruby status, editable-project identity, relative media resolution, audio-load evidence, Japanese MMS input identity and audit/build gates only when the dedicated workflow is selected, ASS/report identity, release fields, and boundary frames.
