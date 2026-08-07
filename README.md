[简体中文](README.zh-CN.md) | English

# Karaoke AV1 Video Production Skill

A Codex skill and StrangeUtaGame integration for producing, reviewing, rendering, validating, and packaging karaoke videos with editable timing provenance and AV1 4:2:0 release checks.

The bundled public distribution provides two parallel Japanese (`ja`) single-track entries. `run_karaoke_japanese_workflow.py` is the default entry and never runs MMS. `run_karaoke_japanese_mms_workflow.py` is the installed explicit MMS entry; it runs `audit -> build -> render` from an existing manifest, canonical SUG, frozen lyrics, and project-local MSST Vocals. The public distribution path is currently verified for Japanese (`ja`) only; additional language workflows require their own validated adapters and are not part of this distribution.

## Capabilities

- Inspect → preview → encode → verify production flow.
- Semantic phrase segmentation, Japanese ruby word-boundary review, editable SUG parity, explicit MMS audit/override evidence, independent ASR review, and visual-fit checks.
- Deterministic offline cover extraction with an ordered eight-colour palette, cover/extractor identities, and one shared `karaoke-color-plan/v1` for every supported profile.
- Explicit multi-singer SUG `singer_id` routing with character → sentence → project-default effective-singer resolution, first-character slot allocation for active singers only, explicit colour precedence, consistent active colours across Main/Glow/cues/top overlays, and white inactive text.
- Dedicated top-centred `opera`/`harmony`/`secondary` overlays use the `y=0..96` safe band at anchor `y=12`, a default `60 px` font, and a `36 px` minimum for long-line fitting; the actual outline/glow reserve extends through `y=107`; cross-singer ruby is rejected.
- Mutually exclusive rotating-vinyl and real-time-spectrum wide layouts; formal rendering uses the cover colour source by default, while `project` is the rollback compatibility mode.
- `wide-layout-v7/cover-palette`: no outer right panel and no compact backplate behind the vinyl; the album card, footer, rotating record, and lower subtitle panel remain. The title label/title/artist positions are `y=120/155/220`; use actual title ink bounds and keep at least `16 px` between the title ink and the secondary reserve.
- Clip-safe spectrum geometry with top and bottom glow clearance.
- Default 1920x1080, 30 fps, `yuv420p`, BT.709 AV1 delivery with AAC-LC 320 kb/s audio.
- MP4 as the default output; MKV only when explicitly selected with a verified FLAC or PCM WAV source.
- Optional complete-output decode diagnostics; ordinary verification uses probes, sampled decoding, frame inspection, and output identities.
- Pronunciation/ruby validation is optional by default; the Japanese structural ruby gate remains mandatory, while `required` and `off` are explicit modes.
- Complete-mix pitch shifting through `scripts/pitch_shift_audio.py` with Rubber Band R3 Finer and formant preservation.
- JSON-based album configuration and song-specific display, timing, and ruby decisions.
- Shared single-track one-click and AV1 batch rendering over the same renderer, with separate preflight/final ASS generation and colour-plan identity parity.

## Colour plan

The deterministic offline cover extractor emits an ordered palette of exactly
eight colours plus `cover_sha256` and the current extractor hash. The renderer
builds exactly one `karaoke-color-plan/v1`; one-click and batch are entry
points to this same implementation.

The extractor excludes near-black chroma noise and aggregates candidate area
across neighbouring colours in Lab space. A rare JPEG-noise sample must not be
brightened into the primary colour.

After effective singer resolution, assign primary, secondary, and tertiary
slots by the first lyric-character appearance of active `singer_id` values;
an absent singer consumes no slot. Apply this precedence:
`explicit singer_id=#RRGGBB` > explicit main or secondary slot override >
cover palette > project-policy SUG colour. The primary colour follows the
first singer and spectrum. The secondary follows the second singer, or
`palette[1]` for a single singer, and the progress track.

Composition metadata contains `cover_sha256`, the current extractor hash, and
the ordered `palette`. ASS, video, and workflow outputs carry the same
`color_plan_sha256`; colour planning does not change the source SUG. Stale or
inconsistent cover, extractor, palette, or colour-plan metadata fails closed.

## Install

Clone the repository into the Codex skills directory:

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

Invoke it in Codex with:

```text
$karaoke-av1-video-production
```

Preview the StrangeUtaGame integration copy plan, then install it:

```powershell
$skillRoot = (Resolve-Path .).Path
$projectRoot = (Resolve-Path .\project).Path
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target $projectRoot --dry-run
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target $projectRoot
```

## Environment

Reuse the existing project-local `.venv`. Run `uv sync` only when the environment is absent or dependency files changed; use `uv run --no-sync` for ordinary commands:

```powershell
$projectRoot = (Resolve-Path .\project).Path
Set-Location $projectRoot
if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
  uv sync
}
uv run --no-sync python --version
```

Install `ffmpeg` and `ffprobe` separately. Rubber Band is needed for pitch shifting; Whisper and external MSST are optional evidence lanes. The default one-click and batch routes never generate, consume, or validate MMS. The explicit MMS entry described below is offline by default; standalone audit/build scripts remain available for evidence preparation. Check the target environment with:

```powershell
$skillRoot = (Resolve-Path .).Path
$projectRoot = (Resolve-Path .\project).Path
Set-Location $projectRoot
uv run --no-sync python "$skillRoot/scripts/check_karaoke_environment.py" --target $projectRoot
```

## Workflow

1. Supply the album manifest and any display, timing, or ruby override JSON through explicit paths or environment variables.
2. Probe source media and select the output profile.
3. Build or update the canonical SUG, then review phrase segmentation and applicable ruby spans.
4. Run the deterministic offline cover extractor, record the ordered eight-colour palette and cover/extractor identities, and build the shared colour plan.
5. Choose the timing-evidence lane explicitly: the default one-click and batch routes never run MMS; the installed MMS entry requires an existing manifest, SUG, frozen lyrics, and MSST Vocals and runs `audit -> build -> render`; standalone MMS audit/build scripts, independent ASR, and MSST-derived evidence remain separate evidence tools.
6. Build the current wide composition and regenerate the current rotating vinyl asset when using the vinyl layout.
7. Render an isolated preview, inspect representative frames, and encode the selected MP4 output.
8. Verify media structure and sampled output, then finalize, promote, or package the accepted files.

Example manifest configuration:

```powershell
$env:KARAOKE_ALBUM_MANIFEST = (Resolve-Path .\config\album.json).Path
uv run --no-sync python scripts/karaoke_timing.py --manifest $env:KARAOKE_ALBUM_MANIFEST --allow-partial-manifest
```

## Single-track workflow entries

### Default single-track command

The bundled one-click route is `scripts/run_karaoke_japanese_workflow.py`.
It defaults to `--visual-style vinyl`; both visual styles require a new
non-existent output directory:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --composition <composition-png> --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style vinyl --vinyl <canonical-vinyl-png>
```

For spectrum, use `--visual-style spectrum` and omit `--vinyl`. Formal
rendering uses the cover colour source by default; `project` is the rollback
compatibility mode. Vinyl uses `--vinyl` as a canonical identity input,
rebuilds and validates the current rotating vinyl in the new output directory,
and passes the generated asset to the renderer. Spectrum does not require,
probe, generate, pass, or report vinyl.

The workflow first builds the shared `karaoke-color-plan/v1` and writes the
independent `karaoke-preflight.ass`, then the final `karaoke.ass` during
rendering. ASS, video, and workflow outputs carry the same
`color_plan_sha256`; a stale or inconsistent composition colour record fails
closed, and the source SUG is unchanged. Full duration and MP4-only output
with AAC-LC audio are the defaults. MKV and full decode require explicit
opt-ins; default runs do not create MKV or perform full decode. Pronunciation/
ruby validation is optional by default; Japanese structural ruby checks remain
mandatory, while `required` and `off` are explicit choices. The one-click and
batch entries use the same renderer and gates. Repeat `--singer-color
<singer-id>=#RRGGBB` on either entry only when an explicit per-singer override
is required; it takes precedence over slot and palette colours.

The default entry has no MMS parameters. It never generates, consumes, or
validates MMS. `audit_karaoke_mms_alignment.py` and
`build_karaoke_mms_overrides.py` remain explicit independent scripts; use them
only as a separately requested evidence lane or through the explicit entry
described next.

### Explicit MMS single-track workflow

`scripts/run_karaoke_japanese_mms_workflow.py` is the installed explicit MMS
entry in the public integration.

Before invoking it, these inputs must already exist and be resolvable from the
selected project configuration:

- the manifest and its selected source audio;
- the canonical reviewed SUG;
- the frozen lyrics used for the audit;
- project-local MSST Vocals with their own provenance.

Every run writes to a new, non-deliverables output root. It must not write
directly to a deliverables directory or reuse an earlier output root. The
wrapper creates exactly `audit/`, `build/`, and `render/` beneath it;
`render/` is the final-video working directory. The stages are ordered and
mandatory:

```text
audit -> build -> render
```

`audit` runs MMS against the existing SUG, frozen lyrics, source audio, and
MSST Vocals. The audit gate fails closed when a required input or identity is
missing, stale, mismatched, unresolved, or vetoed. `build` may run only from a
passing audit and creates `build/timing_overrides.json`. The build
gate carries the manifest, SUG, frozen lyrics, MSST Vocals, MMS access policy,
and audit identity forward.

Of the MMS build outputs, only the `visual_release_overrides_ms` field is
copied into the render input and may affect the ASS/video; it is a conceptual
field in the build artifact, not a directory name. `character_overrides_ms`
remains audit/build evidence and provenance; it is not applied to the SUG,
ASS timing, or encoded video. The render gate requires the passing build gate
and matching provenance, then records audit/build/render identities in the
output report. No stage may be skipped or silently substituted by the default
route.

MMS model access is offline by default. The optional
`--mms-model-path <local-mms-model>` override has highest priority. Without
it, the wrapper first discovers the project-local
`.cache/torch/hub/checkpoints/model.pt`, then another local `.pt` checkpoint
in that directory. It fails before inference only when no local checkpoint is
available and `--allow-mms-network` was not granted. Cover extraction has a
separate policy and remains offline unless `--allow-cover-network` is passed.
Neither permission authorizes the other lane, and the wrapper accepts no
generic model-path or network-permission aliases.

The installed wrapper CLI is:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <existing-manifest> --song-id <song-id> `
  --composition <composition.png> `
  --output-dir <new-non-existent-mms-output-dir> `
  --visual-style spectrum
```

The manifest resolves the selected track's canonical SUG and source audio;
by default the wrapper resolves frozen lyrics from the selected manifest
deliverable's `sources/netease_lyrics.json` and MSST Vocals from the project's
`.cache/msst-vocals` tree. Use `--source <frozen-lyrics>` and
`--vocals-root <msst-vocals-root>` only as explicit overrides. The model
override is likewise optional. The only network permissions are
`--allow-mms-network` and the independent `--allow-cover-network`.

## AV1 4:2:0 batch command

The AV1 4:2:0 batch entry is
`scripts/render_karaoke_direct_av1_420_album.py`. It accepts
`--visual-style vinyl|spectrum|both` and defaults to `vinyl`:

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <album-manifest> `
  --visual-style <vinyl|spectrum|both>
```

This batch entry uses the same renderer and `karaoke-color-plan/v1` builder as
the one-click command; it is not a second workflow. `spectrum` does not
require, probe, generate, pass, or report a vinyl asset. `both` runs the vinyl
and spectrum styles as two independent AV1 4:2:0 outputs with distinct media
and report identities. The two styles for one song/profile reuse the same
colour plan and profile ASS and publish serially; they are not a combined
visual effect. `--single-track` selects exactly one song and one profile, so
`--single-track --visual-style both` can produce two style variants for that
one song/profile:

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <album-manifest> --song <song-id> --profile wide `
  --single-track --visual-style both
```

`--lossless-companion` and `--full-decode` remain explicit opt-ins for the
selected style or styles. Neither option is implied by `both`. Apply the
per-output release and rollback gates in
[batch-release-gates.md](references/batch-release-gates.md).

Formal AV1 4:2:0 batch rendering does not run MMS. If the fixed path
`<album-root>/sources/timing_overrides.json` exists, the batch renderer
automatically consumes its existing visual-release overrides (and records the
file identity); this is not an MMS run, audit, or parameter, and the batch
renderer does not create the file.

## References

Each reference has matching English and Chinese versions:

| Topic | English | 中文 |
|---|---|---|
| AV1, FFmpeg, MP4/MKV | [av1-420-commands.md](references/av1-420-commands.md) | [av1-420-commands.zh-CN.md](references/av1-420-commands.zh-CN.md) |
| SUG, independent ASR, pitch | [asr-sug-pitch.md](references/asr-sug-pitch.md) | [asr-sug-pitch.zh-CN.md](references/asr-sug-pitch.zh-CN.md) |
| Wide vinyl/spectrum | [wide-visual-templates.md](references/wide-visual-templates.md) | [wide-visual-templates.zh-CN.md](references/wide-visual-templates.zh-CN.md) |
| Singer identity and secondary overlays | [singer-overlays.md](references/singer-overlays.md) | [singer-overlays.zh-CN.md](references/singer-overlays.zh-CN.md) |
| Subtitle timing and quality | [subtitle-timing-quality.md](references/subtitle-timing-quality.md) | [subtitle-timing-quality.zh-CN.md](references/subtitle-timing-quality.zh-CN.md) |
| Batch release | [batch-release-gates.md](references/batch-release-gates.md) | [batch-release-gates.zh-CN.md](references/batch-release-gates.zh-CN.md) |
| StrangeUtaGame integration | [strangeutagame-integration.md](references/strangeutagame-integration.md) | [strangeutagame-integration.zh-CN.md](references/strangeutagame-integration.zh-CN.md) |

## Integration file map

The dependency manifest is authoritative for the installed file set.

| Stage | Entry or module |
|---|---|
| Configuration and text | `karaoke_album.py`, `karaoke_language.py` |
| Timing and editable SUG | `karaoke_timing.py`, `karaoke_review_preview.py`, `sync_karaoke_editable_ruby.py`, `sug_ruby.py` |
| Alignment evidence | `audit_karaoke_asr_recognition.py`, `audit_karaoke_mms_alignment.py`, `build_karaoke_mms_overrides.py`, `prepare_karaoke_msst_vocals.py` |
| Artwork and rendering | `karaoke_cover_palette.py`, `karaoke_color_plan.py`, `build_karaoke_wide_artwork.py`, `render_vinyl_karaoke.py`, `karaoke_direct_album_planning.py`, `render_karaoke_direct_av1_420_album.py`, `render_karaoke_direct_hevc444_album.py` |
| Japanese workflow | `karaoke_workflow.py`, `run_karaoke_japanese_workflow.py`, `run_karaoke_japanese_mms_workflow.py` |
| Media and release | `inspect_karaoke_media.py`, `transcode_karaoke_av1.py`, `finalize_karaoke_release.py`, `karaoke_release_snapshot.py`, `package_karaoke_numbered_archives.py` |
| Pitch shifting | `pitch_shift_audio.py` |

Recursive package files are `karaoke_common/__init__.py`, `karaoke_common/layout.py`, `karaoke_common/pronunciation.py`, `karaoke_japanese/__init__.py`, and `karaoke_japanese/layout.py`.

Repository support tools are `check_sug_compatibility.py`, `check_karaoke_environment.py`, `install_strangeutagame_integration.py`, `open_editable_project_with_audio_probe.py`, and the standalone mirror of `pitch_shift_audio.py`.

For direct album rendering, use `render_karaoke_direct_av1_420_album.py` for AV1 4:2:0 or `render_karaoke_direct_hevc444_album.py` for HEVC 4:4:4. Shared manifest selection and task planning live in `karaoke_direct_album_planning.py`.

## Repository layout and tests

```text
.
├── SKILL.md
├── LICENSE
├── NOTICE.md
├── THIRD_PARTY_NOTICES.md
├── agents/
├── examples/
├── integration/strangeutagame/
├── references/
├── scripts/
└── tests/
```

```powershell
uv run --no-sync python -m unittest discover -s scripts -p "test_*.py" -v
uv run --no-sync python -m unittest discover -s tests -p "test_*.py" -v
```

## License

The repository code and documentation use GPL-3.0-only. See [NOTICE.md](NOTICE.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
