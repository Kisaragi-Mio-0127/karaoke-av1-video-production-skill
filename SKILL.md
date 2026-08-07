---
name: karaoke-av1-video-production
description: Use when producing, re-encoding, debugging, packaging, or reviewing karaoke and lyric videos or opening editable karaoke timing projects with attached audio, including semantic phrase segmentation, explicit SUG singer_id colour routing, dedicated Japanese secondary-vocal overlays, cross-singer ruby rejection, source-line overrides, cue/lane behavior, Japanese ruby, editable-source/render parity, media restoration, MMS timing evidence, ASS highlight release, lyric visual fit, AV1 4:2:0 encoding, batch promotion, archives, and media-structure verification. Do not use for TTS, voice cloning, music generation, vocal separation, or lyric transcription.
---

# Karaoke AV1 Video Production

Read the [repository README](README.md) or the [中文 README](README.zh-CN.md)
for installation, the public script inventory, and the bilingual reference
map.

## Overview

Produce karaoke videos through an inspect, preview, encode, and verify workflow. Keep subtitle timing, AV1 4:2:0 output, audio integrity, and playback compatibility independently testable.

The generic default remains Japanese (`ja`) for legacy manifests. Use
`run_karaoke_japanese_workflow.py` for the bundled Japanese route. Any other
language requires a separately validated adapter; never silently fall back to
another language profile or workflow.

Use `tts-voice-workflow-ops` separately when generating or cloning voices, separating vocals, or converting a singer. This skill starts from authorized media, lyrics, subtitles, fonts, and audio stems.

Read [av1-420-commands.md](references/av1-420-commands.md) / [中文](references/av1-420-commands.zh-CN.md) when constructing FFmpeg or ffprobe commands.

Read [wide-visual-templates.md](references/wide-visual-templates.md) / [中文](references/wide-visual-templates.zh-CN.md) before selecting or changing the wide-layout vinyl or real-time spectrum template. Keep the two effects mutually exclusive and drive both through the shared artwork and preview-render scripts.

The current `wide-layout-v6/top-secondary-clearance` composition uses a vinyl card at
`(40,30,340,402)`, footer bottom padding `12`, and a lower subtitle panel that
starts at `y=576`. Both the extra outer right-panel overlay and the compact
dark backplate behind/below the rotating record are removed. The rotating
record, album card, card footer, and bottom subtitle panel remain. Reports use
`right_panel_visible=false`, `outer_right_panel_visible=false`,
`vinyl_backplate_present=false`, and the compatibility field
`vinyl_backplate_preserved=false` for this no-panel composition. The spectrum
variant must also use the clip-safe geometry documented in
[wide-visual-templates.md](references/wide-visual-templates.md), leaving top
and bottom glow clearance so peaks are not clipped. The top secondary contract
uses anchor `y=12`, default font size `60 px`, minimum long-line size `36 px`,
content safe band `y=0..96`, and an actual outline/glow reserve through `y=107`.
The title block uses actual ink bounds at label/title/artist `y=120/155/220`
and keeps at least `16 px` between title ink and the secondary reserve.

## Shared single-track workflow

For the bundled single-song one-click route, run
`scripts/run_karaoke_japanese_workflow.py` from the StrangeUtaGame checkout.
It accepts `--visual-style vinyl|spectrum` and defaults to `vinyl`. Both
styles require a new, non-existent `--output-dir` for every run:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --composition <composition-png> --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style vinyl --vinyl <canonical-vinyl-png>
```

For `spectrum`, replace the final style and vinyl arguments with
`--visual-style spectrum`; `--spectrum-color RRGGBB` and
`--progress-color RRGGBB` are optional. Vinyl requires `--vinyl` as the
canonical identity input, then rebuilds and validates the current rotating
vinyl inside the new output directory before rendering. Spectrum does not
require, probe, generate, pass, or report any vinyl asset.

The workflow first creates an independent `karaoke-preflight.ass` in ASS-only
mode, then creates the final `karaoke.ass` during MP4 rendering. It compares
their SHA-256 identities for the same SUG/configuration and fails if they
differ. Full probed duration and MP4-only output with AAC-LC audio are the
defaults. MKV and full decode require explicit opt-ins; a default run does not
create MKV or run a full decode. Japanese pronunciation validation defaults to
non-blocking `optional`; `required` and `off` remain explicit choices. The one-click route and the
underlying renderer must apply the same singer, overlay, ruby, container, and
diagnostic gates.

The album/batch direct renderer (`render_karaoke_direct_av1_420_album.py`)
is the AV1 4:2:0 batch entry and accepts `--visual-style vinyl|spectrum|both`,
defaulting to `vinyl`:

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <album-manifest> `
  --visual-style <vinyl|spectrum|both>
```

`spectrum` does not require, probe, generate, pass, or report a vinyl asset.
`both` produces two independent AV1 4:2:0 outputs with separate media and
report identities. Both styles for one song/profile share a hash-identical
profile ASS and publish serially; they are not combined in one file.
`--single-track` means exactly one selected song and one profile, so
`--single-track --visual-style both` can produce two variants for that pair.
`--lossless-companion` and `--full-decode` remain explicit opt-ins and are
not implied by `both`. Apply the per-output batch release gates before
promotion.
Use `render_karaoke_direct_hevc444_album.py` for the explicit HEVC 4:4:4 lane.
Treat `render_karaoke_direct_av1_album.py` only as its deprecated compatibility
name; shared manifest selection and task planning belong to
`karaoke_direct_album_planning.py`, not either codec-specific entry point.

Read [subtitle-timing-quality.md](references/subtitle-timing-quality.md) / [中文](references/subtitle-timing-quality.zh-CN.md) when changing phrase segmentation, cue behavior, ruby, MMS-derived timing, highlight release, visual-fit rules, or opening an editable timing project for review.

Read [singer-overlays.md](references/singer-overlays.md) / [中文](references/singer-overlays.zh-CN.md) when a project has multiple singers, explicit `singer_id` fields, opera/harmony/secondary roles, singer-colour routing, top overlays, or ruby spans that may cross singers.

Read [asr-sug-pitch.md](references/asr-sug-pitch.md) / [中文](references/asr-sug-pitch.zh-CN.md) before using independent ASR evidence, validating a newer StrangeUtaGame/SUG version, or pitch-shifting delivery audio. Use the bundled `scripts/check_sug_compatibility.py` and `scripts/pitch_shift_audio.py` instead of one-off commands.

For StrangeUtaGame editor review, use [open_editable_project_with_audio_probe.py](scripts/open_editable_project_with_audio_probe.py) to open the exact SUG through the real command-line loader and record playback-engine audio evidence without saving the project.

Read [batch-release-gates.md](references/batch-release-gates.md) / [中文](references/batch-release-gates.zh-CN.md) when encoding, promoting, or packaging more than one song or delivery profile.

Read [strangeutagame-integration.md](references/strangeutagame-integration.md) / [中文](references/strangeutagame-integration.zh-CN.md) before installing or running the bundled production scripts. Use [install_strangeutagame_integration.py](scripts/install_strangeutagame_integration.py) to copy the integration into a compatible StrangeUtaGame checkout; dry-run first and do not overwrite differing project scripts without a backup. Run [check_karaoke_environment.py](scripts/check_karaoke_environment.py) from the Skill repository after installation (the support tool is not copied into the target checkout).

## First Pass

1. Read project instructions and inventory source video or images, audio tracks, lyrics, existing ASS/SRT files, fonts, and target platforms.
2. Build a rights manifest for the background media, recording, lyrics synchronization/display, subtitle source, and fonts. Record source, rightsholder or license, evidence, allowed use, commercial scope, territory, term, attribution, and redistribution limits. Stop public delivery when any required right is missing or uncertain.
3. Probe every input for duration, frame rate, resolution, color metadata, codec, pixel format, sample rate, channel layout, and start-time offsets.
4. Define the intended output matrix before encoding: hard or soft subtitles, MP4 or MKV, 8-bit or 10-bit 4:2:0, audio codec, resolution, and compatibility fallback.
5. Write to a temporary output and preserve source media until all mandatory gates pass. Full-output null decoding is an optional diagnostic, not a release gate.
6. Keep probes, ASS sources, fonts, and encode logs private by default. Do not include them in delivery unless their redistribution is authorized.
7. Before full encoding, run project-specific renderer and packager tests with a writable project-local temporary directory. Treat setup errors, skips, interrupted runs, and partial results as inconclusive.
8. Prefer `uv run --no-sync ...` for every command; it reuses the existing complete project-local `.venv` without creating or syncing an environment. Do not create a new environment per task or set a new `UV_CACHE_DIR` for ordinary runs. Run `uv sync` only when `.venv` is missing or dependency files actually changed. Use task-owned project-local temporary directories/caches and remove them after reports and artifacts are retained.

## Editable Project Gate

- Skip manual timing review by default when automatic timing evidence and release checks agree. Enter manual timing only when the user requests it, a reported subtitle defect needs listening, or automatic evidence conflicts. When manual timing is required, first prove that the exact editable project opens and its stored media reaches the playback engine.
- Treat the editable timing project (`.sug`, `.kra`, or equivalent) as a fact-chain layer, not as an incidental export. The candidate ruby generator fills missing ruby only and writes it to the canonical SUG; preserve existing human-reviewed or legacy ruby. The Agent then audits every ruby span in full-lyric context and writes approved corrections back to that SUG before rendering.
- Make the renderer read-only for ruby: it reads the reviewed canonical SUG and must not infer, contextualize, or overwrite ruby during rendering. Bind the review sidecar to the current canonical SUG hash and require one approved, span-exact record for every stored ruby span; a missing or stale sidecar, `machine-fill`, low-confidence, conflict, or unresolved record blocks rendering. If the Agent makes no change, retain the candidate generator's default ruby but still record its approval. Before opening an editor, identify the canonical project by content and generation identity, compare its reviewed ruby/timing with the ASS and render report, and keep intentional corrections in the editable project.
- Resolve a relative media path against the editable project file's parent directory. Exercise the actual startup path used by the requested launch mode; command-line, file-menu, drag-and-drop, and recovery flows may use different loader implementations.
- Inspect autosave and crash-recovery copies before launch. Preserve genuine user edits, but do not allow a stale recovery copy to silently replace a newer reviewed project.
- Verify that audio entered the playback engine using an engine-observable result such as populated audio metadata, waveform data, a newly generated source cache, or a controlled playback check. A visible window, project title, existing file, or stored path is not proof of audio loading.
- Prefer a project-local launch/probe script over UI file dialogs when the editable project already stores `media_path`. For StrangeUtaGame, run the bundled audio-probe script with `uv run --no-sync`; use `uv sync` only to create or refresh a missing/stale environment. Require it to isolate settings/cache/recovery state, install auto-save and canonical-save guards before opening, and report exact opened-project identity, project/audio hashes, resolved and callback media paths, engine metadata, non-empty finite waveform evidence, dirty transition, and project hashes.
- Allow a review helper to bypass an unrelated startup update check only in process and only to unblock the requested project/media restoration path. Do not change updater settings, start an update, or treat the bypass as application validation; record it in the probe report.
- Never auto-save an editor review session. If audio loading alone changes the in-memory duration by a rounding-sized amount such as 1 ms, changes dirty from false to true, and leaves all other serialized project fields unchanged, leave it unsaved and record `do-not-save-duration-normalization`. Treat any other dirty state as requiring review before save. A callback pass is provisional while the editor remains open; require graceful exit plus a final unchanged canonical hash for the completed gate. A forced exit or missing final check is inconclusive.
- Keep guarded loading review separate from editable work. The probe writes only a private JSON evidence report and blocks project saves. If the user actually adjusts timing, reopen the verified canonical project in the application's normal editable mode and save those edits to the canonical editable source (`.sug` for StrangeUtaGame); then regenerate the ASS and final video from that saved source. Do not mistake the probe JSON, ASS, or encoded video for the editable timing source.
- Report separately: project opened, reviewed project identity matched, media path resolved, audio engine load verified, and any recovery copy handled.
- Require StrangeUtaGame 1.4.5 and SUG storage format 0.3.0. Keep the application version synchronized in `src/strange_uta_game/__version__.py` and `pyproject.toml`, and read the storage format from `SugMigrator.CURRENT_VERSION`. Run the compatibility checker against representative canonical projects after an application update; loading must leave every project hash unchanged.

## Subtitle And Timeline Gate

- Keep subtitle sources in UTF-8 and preserve editable ASS or timing files beside the rendered output.
- Use ASS karaoke timing tags only when syllable timing is intentional; do not invent timing from untimed lyrics without review.
- Keep one traceable fact chain from canonical source through overrides, renderer output, ASS/report, encoded media, and promoted or packaged artifacts. Record a hash or equivalent generation identity at every reproducible layer.
- Preserve source-line semantics when creating display phrases. Require exact override reachability, lossless phrase recomposition, and reuse of original character timing objects.
- Treat source whitespace as breath evidence, not an authoritative display boundary. Resolve it against source-language syntax, measured acoustic pauses, minimum phrase length, and visual fit; document reviewed exceptions where a sung breath splits a grammatical dependency.
- Resolve singer identity only from explicit SUG data, with character-level `singer_id` taking precedence over sentence-level `singer_id`, then the explicit project default. Apply the resolved singer colour consistently to active `Main`, `Glow`, cue, and top secondary subtitle layers; keep inactive or unhighlighted text white. Never infer singer identity from lyric text or role wording.
- Route explicit `opera`, `harmony`, and `secondary` roles to a top-centred overlay independent of main lanes, cues, and ruby. Use the top safe band `y=0..96`, anchor `y=12`, default font size `60 px`, and a minimum `36 px` long-line size; the actual outline/glow reserve extends through `y=107`. Inspect coexistence with the `wide-layout-v6/top-secondary-clearance` title, whose label/title/artist positions are `y=120/155/220`, whose placement uses actual ink bounds, and whose title ink stays at least `16 px` below the reserve.
- Treat ruby word boundaries as a mandatory release gate, independently from reading correctness. The candidate generator fills missing ruby only; preserve existing human-reviewed or legacy ruby. The Agent audits every ruby span like semantic phrase segmentation, using the full lyric sentence, grammar, inflection, lexical word boundaries, and context, and may approve it or write a correction directly back to the canonical SUG. Human review is not mandatory for every span: escalate only ambiguity, proper nouns, artistic readings, evidence conflicts, low confidence, or `unresolved` results. If no correction is made, retain the default ruby. Do not force one ruby group per kanji: keep a multi-kanji lexical word or jukujikun such as `今年→ことし`, `来年→らいねん`, or `一番→いちばん` together. Do not merge adjacent lexical words merely because their readings are contiguous; for example, keep `一番|好|き` as `一番→いちばん`, `好→す`, and unannotated okurigana `き`, rather than `一番好→いちばんす`. For StrangeUtaGame, inspect every ruby-bearing line's canonical SUG `linked_to_next` chain, compare the same surface spans and readings in the ASS/report, and inspect a rendered frame to confirm each ruby is centered over the intended word. The renderer reads only the reviewed canonical SUG and must not infer or override ruby. Record per-span status, confidence, evidence, model/prompt version, and before/after SUG hashes; fail release when the SUG, ASS/report, or final frame disagrees, even if the concatenated reading text is correct, while preserving character timing unless timing is the explicit task.
- Apply extra in-line semantic spacing only at approved breath or semantic boundaries. Record the boundary character indices and one configured pixel/em value, then verify coordinate deltas independently from perceived spacing caused by glyph shape, highlight state, or ruby.
- Reject a ruby chain when its resolved surface characters contain more than one `singer_id`; never split or silently reassign a cross-singer span during rendering. Run this check in the one-click preflight, final render, and lower-level renderer gate.
- Treat cue pairing, lane reset, countdown anchoring, ruby, target font sizes, display preload, per-character sweep onset/release, line release, event end, and outro visibility as separate explicit contracts rather than incidental renderer behavior. A report that a red sweep is early or short does not authorize moving the lyric display preload; change preload only when the user reports that lyric visibility itself is wrong.
- When the established album layout preloads intro lyrics from program start and interlude lyrics from the preceding visible event end, preserve those starts independently of the later countdown-dot window and acoustic onset. Keep the approved outro marker visible from the final lyric event end through the media end unless the user explicitly requests a clean tail.
- For a held syllable reported as too short, inspect the following character's sweep onset as well as the current character's release. If the following onset was assigned inside the held vowel, move the reviewed following onset (or a renderer-only visual onset) so the held glyph consumes the sustain; do not misuse a line-level preload or event boundary to create the effect. Distinguish sustained sound from a silent breath before accepting the change.
- When the user requests a cover-derived highlight colour, record one approved RGB hex and its extraction or selection basis. Synchronize it across the editable singer colour, ASS `Main`/`Glow` primary colour, and active cue colour while retaining the approved unhighlighted, outline, alpha, and ruby colours; verify the RGB-to-ASS BGR conversion and inspect a partial-sweep frame.
- For wide-layout karaoke output, fix the default typography at `1.5x` the project's established `1x` baseline for main lyrics, ruby, and countdown cues. For the current 72/34/26 px baseline, require 108/51/39 px respectively. Use a 35 px ruby-to-main anchor gap and place countdown cues 16 px above the ruby anchor. Apply both spacing values consistently to upper, lower, outro, and cue layouts. Do not switch to `2x`, silently shrink, or change only one of these layers unless the user explicitly requests it or measured overflow is accepted as a recorded, rollback-safe exception.
- Treat original-mix and separated-vocal MMS results as timing evidence, not delivery tracks. Resolve conflicts with recorded confidence and human A/B review.
- Use the configured language profile for the documented ASR and alignment path; require a validated adapter for any non-default profile. Keep independent ASR separate from stable-ts and MMS forced alignment; it may support, veto, or remain unresolved, but is never a silent fallback that replaces failed alignment. If ASR is unavailable or errors, record `unresolved` and require other evidence or human review.
- In StrangeUtaGame, pronunciation validation is an explicit `optional`, `required`, or `off` mode and defaults to non-blocking `optional`. `optional` keeps structural ruby checks and SUG/ASS/frame agreement mandatory, but a missing semantic sidecar is recorded as not performed and does not block release. Use `required` only when explicitly requested; use `off` to disable semantic-sidecar review.
- Resolve CJK fonts explicitly and inspect missing-glyph or fallback-font warnings.
- Correct odd dimensions by an explicit pad or scale decision before subtitle rendering; prefer padding when cropping would discard content.
- Render a short preview covering the title, first lyric, longest line, representative lyric changes, dense timing, and ending before the full encode.
- When a rotating disc or other periodic artwork is generated, require rotationally continuous source art: no unintended transparent wedge, partial shadow/highlight arc, colour sector, or seam that sweeps around as the asset rotates. For an opaque disc on a transparent square canvas, verify that every pixel safely inside the disc is fully opaque and the surrounding canvas remains transparent; semi-transparent details must be alpha-composited over the opaque surface instead of replacing its alpha. Inspect the source PNG with alpha visible and compare frames at four quarter-period phases. Bind the render report to the exact artwork path and SHA-256; reusing the same filename is allowed only after regenerating the image and refreshing its identity, never as proof that the pixels are unchanged or correct.
- Keep vinyl rotating (`vinyl_motion: rotate`); `static` describes only a background/composition layer, not the Japanese delivery default. Every formal and test run must rebuild the current vinyl with style `direction-neutral-concentric-grooves/v3/backplate-absent`, record the generator and `vinyl_sha256`, and pass that exact generated path via `--vinyl`. The canonical/old `vinyl.png` is identity-only and must never be silently reused. Do not reintroduce either removed right-side backing panel. If cover extraction uses another audio file, record it as `cover-source-audio` separately from delivery audio.
- Check safe margins, line wrapping, outline/shadow, contrast, and whether lyrics cover faces or essential content.
- Preserve source timing unless a deliberate constant-frame-rate conversion or offset correction is documented.

## AV1 4:2:0 Gate

- Prefer `yuv420p10le` for 10-bit software encoding and `p010le` as the 10-bit NVENC input format. Use `yuv420p` for an 8-bit compatibility variant.
- Never assume the encoder retained 4:2:0; verify the final `pix_fmt` with ffprobe.
- Use AV1 NVENC for fast previews or delivery when a real probe encode succeeds. Use `libaom-av1` as the CPU fallback and reproducible quality lane.
- Do not infer hardware support from the encoder list alone. Run a short synthetic or source preview encode first.
- Use the default release video profile for 1920x1080 30 fps SDR delivery: AV1 NVENC CQ38 with the preset fixed at `p7`, `tune hq`, VBR, full-resolution multipass, lookahead 32, spatial and temporal AQ, AQ strength 8, GOP 240, `yuv420p`, and BT.709 color metadata. Verify every value with `ffprobe`; use another profile only when explicitly requested and recorded.
- Keep speed and image quality claims separate. Use `libaom-av1` as the CPU fallback and reproducible quality lane, and do not infer hardware support from the encoder list alone; run a short probe encode first.
- Preserve documented HDR metadata. For ordinary SDR sources, do not introduce HDR or conflicting color tags; verify expected BT.709 metadata when applicable.

## Audio And Container Gate

- Mix stems before final muxing and check gain, clipping, channel layout, sample rate, silence, and start/end synchronization.
- Map intended streams explicitly. Default to one selected audio stream; when preserving multiple tracks, enumerate and validate every track. Do not rely on FFmpeg automatic stream selection.
- When independent video and audio durations differ, choose and document trim, pad, loop, or stop behavior. Do not use `-shortest` to hide an unresolved timeline mismatch.
- Inspect non-zero start times and preserve relative offsets unless one documented timeline normalization is applied consistently. Decide whether VFR is preserved or converted to an explicit CFR.
- Make the default compatibility delivery MP4 with AAC-LC at 320 kb/s. Keep `--output` and ordinary playback/package references pointed at this MP4. MKV is never an implicit companion: create or report it only after the user explicitly requests `--lossless-companion` (or an underlying explicit `--lossless-output`) and probing proves FLAC or PCM WAV.
- For an explicit MKV request, copy the encoded video stream from the verified MP4 and encode FLAC directly from the same trimmed source-audio interval; never transcode MP4 AAC to FLAC or label a lossy-source conversion as lossless. Without the opt-in, do not create, expect, or report an MKV.
- Reject an explicit MKV request when the source codec is MP3/AAC or otherwise lossy, even when its extension claims FLAC/WAV. Preserve the lossless source sample rate and channel structure; do not force the MP4's 44.1 kHz stereo conversion onto the MKV.
- Use MP4 for hard-subtitle platform delivery. Use MKV when preserving ASS as a soft subtitle track or carrying multiple tracks.
- Do not promise complex ASS soft-subtitle styling in MP4; burn it in or switch to MKV.
- Produce an H.264 compatibility fallback when the target device or platform has uncertain AV1 support.
- Keep master, subtitle source, font manifest, encode log, and delivery outputs distinct.
- Validate the default MP4 alone before promotion. Only when the MKV opt-in is present validate MP4 and MKV as one generation: AAC-LC/320k target metadata on MP4, FLAC-only audio on MKV, identical encoded video-stream hashes, decoded MKV PCM equal to the selected lossless source slice, matching timeline bounds within tolerance, and rollback-safe paired publication. Do not use `-shortest` to conceal drift.
- When pitch shifting is requested, run `scripts/pitch_shift_audio.py` on the complete mix before timing/rendering. Require a probed FLAC or PCM source and reject MP3/AAC input for an explicit lossless companion; never relabel a lossy-source transform as lossless. Treat signed semitones literally, use Rubber Band R3 Finer with formant preservation by default for vocals, keep tempo unchanged, and feed the verified FLAC result into timing evidence and default MP4 muxing. Add it to an MKV only when the explicit opt-in is present. Do not separate vocals merely to shift the complete mix.

## Verification Gate

1. Use ffprobe to verify video codec, pixel format, dimensions, frame rate, color metadata, audio codec, channel layout, duration, and subtitle tracks.
2. Do not run a complete null decode by default. Use it only when the user requests it or when probe, mux, transport, or corruption evidence makes it a useful diagnostic. Full decode is never a mandatory release gate: missing full-decode evidence alone must not block promotion, lower verification status, or create a requirement that every artifact carry a decoder exit code. Report an unperformed diagnostic as `performed: false`, never as a successful decode; only an executed diagnostic can pass or fail.
3. Inspect frames at the beginning, representative lyric changes, longest subtitle, and ending.
4. Check audio/video synchronization near both the start and end, not only the first lyric.
5. Confirm dimensions are even, timestamps are monotonic, ASS events remain within the output timeline, and first/last audio-video timestamps differ by no more than the stricter project tolerance or `max(1 frame, 2 audio frames, 50 ms)`.
6. Confirm the output duration is expected, file size is plausible, and no stream disappeared during muxing. When an optional full or sampled decode is performed, map every intended stream and record its exact window and exit code.
7. Keep the temporary output on the same volume as the destination. Promote it only after every mandatory gate passes; retain a rollback path to the previous accepted artifact, and probe the final destination again after promotion.
8. Capture the actual decoder process exit code for every executed full or sampled decode. Do not treat media-info output, an unperformed decode, a missing serialized exit code, or a status flag from a different generation as decode success.
9. Pair timing and overlap metrics with boundary-frame inspection. Do not accept release counts until source, override, ASS, report, and output identities belong to the same generation.

## Reporting

Report redacted input labels, subtitle and font sources, rights evidence status, fact-chain identities, phrase/cue/ruby/release decisions, FFmpeg version/build configuration, encoder and rate-control lane, dependency and redistribution assumptions, pixel format, color decision, audio/container choices, sanitized commands, previews, probe summary, output files, archive verification when applicable, compatibility fallback, remaining risks, and rollback point. Represent full decode with `performed`, `required`, `recommended`, and `reason`; list sampled windows and real exit codes only when actually executed. Do not expose absolute local paths or unapproved media tags.
