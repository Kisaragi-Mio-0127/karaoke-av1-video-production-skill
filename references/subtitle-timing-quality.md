# Subtitle Timing And Quality Gates

[简体中文](subtitle-timing-quality.zh-CN.md) | English

Use these gates for semantic segmentation, cue display behavior, ruby, MMS timing evidence, and adjacent-line highlight release.

## Fact Chain

Track one generation through:

```text
canonical source -> timing/phrase overrides -> renderer output
-> ASS and render report -> encoded video -> promoted/package artifact
```

- Record hashes or equivalent generation identifiers for every reproducible layer.
- Validate source correctness and final rendered correctness separately. A canonical ruby defect may be corrected by contextual rendering, while a source defect can still block reproducibility.
- Do not combine overlap counts, status flags, ASS, reports, or media from different generations.

## Semantic Phrase Contract

- Review Japanese segmentation in full-line syntactic context. Do not split a display phrase across a source line, interlude, conjugation, particle, fixed expression, or project-defined refrain boundary without human approval; punctuation such as a middle dot is not universally a boundary.
- Treat source whitespace and a model-proposed pause as evidence rather than commands. Prefer a semantically complete phrase when it fits, but retain a documented sung-breath split when the measured pause and performance make the dependency clear.
- After whitespace normalization, concatenated display phrases must reproduce the source line character for character.
- Treat recomposition, override reachability, and passing tests as structural evidence only. They do not prove that a Japanese phrase boundary is semantically natural; review object-predicate, modifier-head, particle, conjugation, quotation, and refrain context separately.
- Key manual overrides by the complete source line and assert the expected hit count. When repeated lyrics require different handling, include a stable section or occurrence identity. Fail on unreachable, duplicate, or unexpectedly broad overrides.
- Slice and reuse canonical character records or objects, timestamps, and generation identity; do not reconstruct characters and silently lose timing or ruby metadata.
- Treat long semantically complete phrases as explicit reviewed exceptions. If a project uses a 15–16-character review band, allow it only when the phrase is semantically complete, a reviewer approves it, the resolved target font and requested size fit without clipping, and the reason is recorded. Do not make that band a universal limit.

## Cue And Lane Contract

- Define cue, section, lane phase, display preload time, and semantic lyric onset for the current renderer before applying lane rules.
- At each intro or interlude cue, preload both upcoming display phrases with the same established display `event_start`. That start may be program start, the preceding visible event end, or countdown start; do not assume that a cue's countdown start owns lyric visibility. Do not overwrite the phrases' separate semantic lyric onsets.
- For an album contract that keeps upcoming lyrics visible during instrumental space, start the intro pair at program start and the interlude pair immediately after the preceding visible lyrics finish. Keep countdown dots on their own later window. A complaint that highlighting is early, late, or too short must not change these preload starts unless the user separately reports visibility as wrong.
- When only one phrase remains or a cue intentionally has no second phrase, record the single-phrase exception instead of fabricating a pair.
- Synchronize only the intended cue pair. Do not let generic grouping make ordinary consecutive phrases start together.
- Anchor countdown dots to the first upcoming phrase.
- Reset the lane phase at each project-defined cue or section, then verify the first lane and alternate within the section according to the renderer contract. Mark lane rules `N/A` for renderers without lanes.
- Verify the previous visible event and fade finish before the new cue group becomes visible.
- Define the ending contract explicitly. When an outro marker is required, begin it at the final lyric event end and keep it visible through the approved media end; verify frames on both sides of the handoff and near the final frame.

## Ruby And Visual Fit

- Review Japanese ruby in whole-word and sentence context; do not mechanically apply isolated kanji readings.
- Check reading correctness and lexical grouping as two independent properties. A concatenated reading can be correct while its ruby span is wrong.
- Prefer lexical-word ruby groups, not uniformly per-kanji groups. Keep multi-kanji compounds and jukujikun together when one contextual reading belongs to the whole word, such as `今年→ことし`, `来年→らいねん`, and `一番→いちばん`. A one-kanji group remains valid when that kanji is the actual annotated lexical span.
- Stop a ruby group at every real lexical boundary. Do not merge adjacent words because their kana can be concatenated; reject `一番好→いちばんす` and require `一番→いちばん`, `好→す`, with `き` left as unannotated okurigana. Normally keep kana okurigana outside the ruby span unless a documented renderer or dictionary contract requires otherwise.
- For StrangeUtaGame, derive canonical word spans from each ruby-bearing line's `linked_to_next` chain. Require every chain to be contiguous, terminate at the intended word end, map to one surface span and one contextual reading, and never cross into the next lexical word. Check every ruby-bearing line, not only reported exceptions.
- Use a tokenizer, morphological analyzer, dictionary, or LLM only as boundary evidence. Resolve ambiguous proper nouns, ateji, jukujikun, contractions, and artistic readings in full lyric context and record the accepted manual decision.
- Verify the same word spans and readings at three layers: canonical/editable project, generated ASS or render report, and rendered frame geometry. In the frame, confirm that each reading is centered over only its intended word and does not visually straddle a neighboring word.
- Emit a focused ruby-word-boundary QA artifact containing stable song and source-line identity, occurrence, main-text span, reading, canonical link flags or equivalent grouping, source/editable/rendered status, reviewed exceptions, timing-unchanged status, and representative frame paths. Fail when any layer disagrees even if the visible kana string is otherwise correct.
- Verify visible ruby independently in the final ASS or rendered frames.
- Record structured `source_ruby_status` and `rendered_ruby_status` values so `source wrong, rendered correct` remains distinct from `rendered output wrong`.
- When an editable project is part of review or delivery, require `editable_ruby_status` too. Do not declare a ruby fix complete while the renderer corrects an obsolete or isolated-kanji reading only at render time.
- Diff the editable project's ruby against the current reviewed ASS/report by stable song, source-line, occurrence, character span, and reading. Back-propagate approved contextual readings to the editable source, then reopen that exact generation and inspect the affected line.
- Ruby corrections must not change character timing unless timing is the explicit task.
- Treat requested main-text size, ruby size, spacing, and resolved font as output invariants. Shrink only after measured overflow and record the exception.
- For semantic gaps inside one display line, record the exact post-character indices and configured em/pixel increment. Compare geometry with and without the gap; do not infer unequal spacing from glyph contours, highlight color, or centered ruby alone.
- Fail on silent font fallback, missing glyphs, silent size reduction, or clipping. Capture frames for the longest phrase and all manual fit exceptions.

## Editable Project And Media Load

- Manual timing review is optional and skipped by default when the automatic evidence chain agrees. Require it when the user asks, when a user-reported visual/acoustic defect needs listening, or when timing evidence conflicts. If required, the opening gate below is mandatory before editing.

- Inventory every candidate editable project, autosave, backup, and crash-recovery copy. Compare hashes, modification times, source identities, affected ruby/timestamps, and media fields; do not assume the file under a delivery folder is newest.
- Preserve reviewed corrections across all intended editable layers. Generated ASS correctness cannot compensate for stale ruby or timing in the project the user opens for manual adjustment.
- Resolve project-relative media paths from the project file's directory. Add a focused test for that rule and cover every distinct loader path that duplicates media restoration logic.
- Launch through the repository's intended project-local runtime. For a `uv` project, use its `.venv`/`uv` entry path and avoid global dependency installation.
- When the project stores `media_path`, prefer a deterministic launch helper that opens the exact project through the application's real command-line loader and lets that loader resolve and load the media. Do not replace this with UI file-dialog automation merely because the window is already open. For StrangeUtaGame, use the skill-bundled `scripts/open_editable_project_with_audio_probe.py` with `--repo`, `--project`, and a private `--status` path.
- Run the helper once with the repository's console Python and `--preflight-only`; require `preflight-pass`. Then launch the same command with project-local `pythonw.exe` and without `--preflight-only`, leaving the editor open for listening. Follow the state machine `preflight-pass -> launching -> loaded-awaiting-exit -> pass`. The callback state must prove exact project and media-path identity, positive engine metadata, and non-empty finite/nonzero waveform evidence. `launching`, a window title, a stale report, forced termination, or a missing post-exit hash is inconclusive.
- Permit the helper to suppress an unrelated startup update dialog only by an in-process, session-local patch. Do not persistently disable updates, modify user settings, install an update, or use a separate loader path. Record `update_check_bypassed` in the evidence.
- Treat asynchronous audio loading as unfinished until the engine exposes evidence: audio information, waveform samples, a source-cache artifact with a current timestamp, or successful controlled playback. Do not infer success from a window title, process, notification, or path existence.
- Write a private machine-readable report at every state transition. Record repository/runtime identity, requested and opened project paths, project/audio hashes, stored and engine durations, duration delta, resolved and callback media paths, sample rate, channels, playback-engine class, bounded waveform shape/dtype/finite/nonzero/peak/RMS evidence, dirty state before and after duration sync, auto-save/recovery isolation, blocked save attempts, post-callback hash, post-exit hash, and final exit status. Do not persist PCM samples.
- Do not auto-save during this review. A rounding-sized duration normalization such as `264187 -> 264186 ms` may mark the in-memory project dirty; record `do-not-save-duration-normalization`, leave the window open, and verify the on-disk SUG hash is unchanged. Do not dismiss a larger or unrelated dirty delta as rounding.
- The guarded helper is a load/listen probe, not an editor-save path: it blocks manual and automatic project writes and emits a private JSON report. For actual human timing changes, close the guarded session, reopen the same verified SUG through StrangeUtaGame's normal editable launch, and save to `.sug`. Treat that `.sug` as the editable source of truth; regenerate `.ass` and the final encoded video from it.
- After launch, check whether recovery UI or an autosave replaced the requested generation. If a recovery copy contains real user edits, preserve and reconcile it; otherwise prevent stale state from masking the reviewed project.
- If the editor has multiple open/load entry points, trace the one actually exercised. Fix and test shared path resolution or every duplicate implementation before claiming the problem is resolved.

## MMS Evidence And Timing Overrides

Treat MMS as project-provided machine alignment or timestamp evidence, not as a universal algorithm. Record the tool, model, version, input lane, configuration, and generation identity that produced it.

Keep independent ASR outside this fallback path. Every ASR/alignment run must select exactly one language, `ja`, `zh`, or `en`. stable-ts and MMS are known-text forced alignment; deterministic interpolation is display timing. If independent ASR is unavailable, fails, or cannot confidently match the frozen lyric window, record an unresolved ASR disposition instead of substituting interpolation or forced-alignment tokens. Simplified/traditional conversion is only comparison normalization within `zh`, never a language fallback; preserve Japanese kanji and kana.

中文说明：独立ASR不属于插值后备流程。stable-ts和MMS使用已知文本做强制对齐，确定性插值只负责显示时间。独立ASR无法运行、执行失败或无法可靠匹配冻结歌词窗口时，必须记录为未解决，不能用插值或强制对齐结果冒充ASR。日语比对必须保留汉字和假名，繁简转换只用于中文。中文歌词的字间不能为了计时而插入空格。

Compare, when available:

| Evidence | Onset | Release | Confidence | Decision |
|---|---:|---:|---|---|
| Current timing | | | | |
| Original mix | | | | |
| Separated vocal | | | | |
| Human A/B review | | | | |

- Treat original-mix and separated-vocal MMS lanes as evidence, not as audio tracks to preserve in the delivery.
- Judge onset and release independently. A separated vocal may shift consonant onset or truncate a sustained tail.
- Do not automatically choose the later mix onset or any single lane. Record the accepted value, confidence, override, and reason for rejecting alternatives.
- A user report of an early or late caption authorizes investigation, not a `human_reviewed` label. Describe accepted machine evidence as user-reported plus dual-lane reviewed unless an actual A/B listening decision was recorded.
- Propagate the resolved timing through source/override, ASS, report, and final-output verification.

## Adjacent-Line Highlight Release

- Derive effective visual release from the emitted ASS `\k`/`\kf` timing, event timing, post-roll, and fade. A report `release_ms` or final-glyph onset alone is not sufficient.
- Record at minimum `event_start`, summed karaoke-tag duration, event end, post-roll end, fade end, next visible start, and the resulting effective visual release used for the overlap decision.
- Compare the previous final phoneme or sustained tail, actual highlight end, next-line onset, and fade tail.
- Do not apply a generic clamp when the final glyph onset is later than the next phrase onset. First determine whether the source onset is wrong or the acoustic tail intentionally overlaps.
- Define the project policy for acoustic-tail preservation versus visually clear sentence boundaries.
- Inspect boundary frames around every conflict and perform original-mix/separated-vocal A/B review when timing evidence disagrees.

## Per-Character Visual Sweep

- Keep acoustic character onset, visual sweep onset, visual sweep release, line release, event end, and fade end as separate values. A line may remain fully highlighted and visible after its last active sweep without continuing to animate.
- A held syllable's visible sweep is bounded by the next effective visual onset, not merely by a phoneme token's short aligned end. When the next character was placed inside an audible sustain, review and move that following visual onset (or its source onset when the acoustic alignment itself is accepted as wrong) so the held glyph sweeps through the sustain. Record both old and accepted boundaries.
- Inspect sustained silent gaps inside a source line as well as the final vocal tail. Do not let the previous glyph's `\k`/`\kf` duration consume a breath or silence merely because the next glyph starts later.
- Allow a reviewed per-character visual release to finish before the next acoustic onset. Hold that glyph fully highlighted through the remaining gap, then start the next glyph at its preserved onset.
- When waveform evidence is used, record the evidence lane, activity end, frame size, padding, previous visual release, accepted visual release, removed silent-sweep duration, confidence, and whether the user reported the defect. Do not label it human-reviewed without an actual A/B decision.
- Preserve canonical acoustic timestamps when repairing visual order. Distribute punctuation without a reliable onset and equal-onset plateaus only on the visual axis.
- Quantize to the renderer's real ASS time base before validating order. Require one strictly increasing visual-onset tick per visible character, including long-vowel marks, sokuon, small kana, digits, and punctuation; pre-quantization uniqueness is insufficient.
- Verify the emitted ASS durations and boundary frames immediately before, at, and after every corrected internal release. Count-based reports alone do not prove that the visible sweep stopped at the intended frame.

## Highlight Colour Contract

- For a cover-derived colour, keep the normalized cover hash, extraction or selection method, candidate palette, accepted RGB hex, and reviewer decision. Prefer an identifiable cover hue that remains visibly distinct from unhighlighted text over a near-white raw dominant cluster.
- Convert RGB `#RRGGBB` to ASS `&HAABBGGRR` explicitly and test the conversion. Preserve style-specific alpha while synchronizing the hue across `Main`, `Glow`, and active cue styles.
- When an editable project is part of the fact chain, synchronize its default singer colour with the rendered ASS. Preserve the approved secondary/unhighlighted colour, outline, shadow, and ruby colours unless the user asks to change them.
- Inspect frames before onset, during a partial sweep, and after completion against the actual composed background. A valid hex and adequate black-outline contrast do not by themselves prove that highlighted and unhighlighted glyphs are visually distinct.

## Test Gate

- Run repository renderer, preview, phrase-segmentation, cue, ruby, release, and package tests with a writable project-local `basetemp` or equivalent.
- Require a clean run. Setup errors, skips, missing dependencies, interrupted execution, or partial suites are not passes.
- Add focused assertions for override hit counts and occurrence identity, phrase recomposition, cue-pair synchronization and single-phrase exceptions, lane reset or N/A, requested sizes and configured long-phrase exceptions, structured source/rendered/editable ruby status, every ruby-bearing line's lexical word spans and `linked_to_next` termination, editable-project generation parity, project-relative media resolution across actual loader paths, verified asynchronous audio load evidence, MMS provenance, source/ASS/report identity, effective-release fields, and release boundary frames.
