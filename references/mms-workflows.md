# Japanese Full-Auto and MMS Contract

[简体中文](mms-workflows.zh-CN.md) | English

Run commands from the StrangeUtaGame project root with `uv run --no-sync`.
This public integration contains Japanese and language-neutral workflow files.

## Full-auto first run

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py `
  --manifest <manifest> --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --output-dir .render-work/<new-run-dir>
```

The command resolves the selected Japanese manifest track and performs:

```text
preflight -> MSST vocal stem -> private initial SUG -> MMS audit/build
-> editable companion SUG -> automatic current layout -> AV1 MP4
```

The output root must not exist and must stay below the project's
`.render-work` directory. The run never overwrites the manifest, frozen lyric
source, canonical SUG, accepted media, or model files.

The four shown inputs are required. Artwork is optional: `--cover` selects an
explicit image; otherwise the standard deliverable `cover.jpg` is reused when
present, followed by an embedded-cover lookup in the selected cover audio.
`--background`, `--composition`, and `--cover-source-audio` are advanced
explicit overrides.

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

## Staged MMS route

Use the lower-level wrapper for recovery or inspection:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --sug <private-or-reviewed.sug> `
  --output-dir <new-private-output-dir> `
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

## Existing SUG route

Use `scripts/run_karaoke_japanese_workflow.py` to rerender an already reviewed
or manually adjusted SUG. That route generates the current layout and video
but does not prepare MSST, build initial timing, or invoke MMS.

## Model and cache boundary

- Keep model weights below `models`, never `.cache`.
- Keep MSST decoded input, stems, runtime files, and recognition/alignment
  cache records below `.cache`.
- Do not download a missing model implicitly.
- Record model and artifact identity in reports without using media hashes as
  quality or process gates.

## Japanese ruby and release

Preserve reviewed ruby in the selected SUG. Pure katakana receives no separate
ruby, and ignored stale pure-katakana ruby must not mutate the input. MMS timing
must not rewrite ruby or frozen display text.

The default delivery is an AV1 `yuv420p` MP4 with hard subtitles and AAC-LC.
MKV/FLAC and full null decode remain explicit opt-ins. Generate the current
layout inside each new run and apply the ordinary subtitle, colour, stream,
duration, and representative-frame gates before promotion.
