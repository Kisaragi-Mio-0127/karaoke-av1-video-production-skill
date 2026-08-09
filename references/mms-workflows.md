# Japanese Full-Auto and MMS Contract

[简体中文](mms-workflows.zh-CN.md) | English

Run commands from the StrangeUtaGame project root with `uv run --no-sync`.

## Full-auto first run

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py `
  --manifest <manifest> --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --output-dir .render-work/<new-run-dir>
```

The command resolves the selected Japanese manifest track and performs:

```text
preflight -> MSST vocal stem -> working initial SUG -> MMS audit/build
-> editable companion SUG -> automatic current layout -> AV1 MP4
-> relocatable editable SUG snapshot
```

The output root must not exist and must stay below the project's
`.render-work` directory. The run never overwrites the manifest, frozen lyric
source, canonical SUG, accepted media, or model files.

The manifest, song ID, new output directory, and exactly one lyric input are
required. Use `--source` for frozen JSON or a NetEase refresh destination, or
use `--lyrics-file` for a manual UTF-8 LRC/TXT file. Artwork is optional:
`--cover` selects an explicit image; otherwise the standard deliverable
`cover.jpg` is reused when present, followed by an embedded-cover lookup in the selected cover audio.
`--background`, `--composition`, and `--cover-source-audio` are advanced
explicit overrides.

The source is frozen by default. To explicitly refresh the selected song from
NetEase, add `--refresh-source`; `--source` becomes the destination for the
refreshed JSON. The workflow reads a supported embedded song ID unless
`--netease-song-id <numeric-id>` is supplied. Without the refresh flag, the
full-auto route performs no lyric network request.

Timestamped LRC is preserved. Plain text uses each non-empty line as one lyric
line and receives uniform coarse anchors across the audio before alignment.
Keep the resulting timing in review-required state.

Album display metadata is read from audio tags by default and falls back to
the song title and artist. Use `--metadata-source-audio` for transformed or
tagless delivery audio. Every successful render writes a media-path-verified
SUG under `render/editable-project`.

A first-run song does not need a `lyric_corrections.json`. When no corrections
sidecar exists, the MMS audit records `lyric_corrections_status=not-provided`
with null path/hash and continues from the frozen lyric source. If a corrections
sidecar is present, its path remains an explicit audited input.

Defaults:

- `--quality-policy auto-fallback`
- `--visual-style spectrum`
- MMS checkpoint `models/mms/model.pt`
- alignment model directory `models/whisper`
- derived MSST and runtime data under `.cache`

`auto-fallback` applies usable MMS timing and retains initial timing for
low-confidence or unresolved units. It preserves the original evidence and
reports `rendered-with-fallback`; it does not claim a quality pass. Structural
SUG, subtitle, model, and media failures still stop the run. Pass
`--quality-policy strict` when uncertainty should retain the companion and
stop before rendering.

## Experimental NextFire Japanese backend

`local-mms-fa` is the default. Select the experimental Japanese-only NextFire
backend only with `--mms-backend nextfire-ja-latn` on either the full-auto or
staged command; it is not presented as better than the default. Do not combine
that option with `--mms-model-path`.

The backend loads only the complete local snapshot at
`models/hf/nextfire-mms-ja-latn`. It has no runtime download or fallback,
does not use a general Hugging Face cache, and does not execute remote code.
The same original/MSST-vocal dual-audio audit and `auto-fallback` or `strict`
quality policy apply.

## Staged MMS route

Use the lower-level wrapper for recovery or inspection:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --sug <working-or-reviewed.sug> `
  --output-dir <new-output-dir> `
  --mms-model-path models/mms/model.pt `
  --quality-policy <strict-or-auto-fallback> `
  --visual-style spectrum
```

Without `--sug`, the wrapper resolves the manifest's canonical SUG. With
`--sug`, audit and build provenance must bind to that same explicit single-song
project. The wrapper order is:

```text
audit -> timing override build -> companion SUG -> release decision -> render
```

Create the companion before applying the quality policy. Keep it separate from
the input SUG. If visual release overrides exist, pass the sidecar to render;
otherwise render the companion with its preserved sentence releases.

Both full-auto and staged MMS accept `--output-mode subtitle-overlay`. This
changes only the render stage; audit, MMS alignment, timing-override build, and
companion-SUG creation retain their normal contracts. With no background video,
the output is a silent transparent ProRes 4444 MOV. Add
`--background-video <footage>` to compose directly with FFmpeg into AV1/AAC;
long footage is trimmed and short footage ends on black for the rest of the
song interval. The render probes `av1_nvenc` first, retries `libaom-av1` after
an unavailable or failed hardware attempt, and records the attempt history.

## Existing SUG route

Use `scripts/run_karaoke_japanese_workflow.py` to rerender an already reviewed
or manually adjusted SUG. That route generates the current layout and video
but does not prepare MSST, build initial timing, or invoke MMS.

The existing-SUG route accepts the same `--output-mode subtitle-overlay` and
optional `--background-video` arguments.

## Model and cache boundary

- Keep model weights below `models`, never `.cache`.
- The optional NextFire weights live only at
  `models/hf/nextfire-mms-ja-latn`; do not commit them to the repository.
- Keep MSST decoded input, stems, runtime files, and recognition/alignment
  cache records below `.cache`.
- Do not download a missing model implicitly.
- Record model and artifact identity in reports without using media hashes as
  quality or process gates.

## Japanese ruby and release

Preserve reviewed ruby in the selected SUG. Pure katakana receives no separate
ruby, and ignored stale pure-katakana ruby must not mutate the input. MMS timing
must not rewrite ruby or frozen display text.

Pronunciation review is optional by default. A missing, stale, machine-only,
unapproved, or unreadable ruby-review sidecar is recorded as not performed and
does not block rendering. Select `--pronunciation-validation required` only for
an explicitly requested approval gate. Structural ruby/SUG errors remain hard
failures in every mode.

The default delivery is an AV1 `yuv420p` MP4 with hard subtitles and AAC-LC.
MKV/FLAC and full null decode remain explicit opt-ins. Generate the current
layout inside each new run and apply the ordinary subtitle, colour, stream,
duration, and representative-frame gates before promotion.
