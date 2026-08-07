[简体中文](README.zh-CN.md) | English

# Karaoke AV1 Video Production Skill

A Codex skill and StrangeUtaGame integration for producing, reviewing, rendering, validating, and packaging karaoke videos with editable timing provenance and AV1 4:2:0 release checks.

The bundled workflow uses Japanese (`ja`) and starts from `run_karaoke_japanese_workflow.py`. Additional language workflows require their own validated adapters.

## Capabilities

- Inspect → preview → encode → verify production flow.
- Semantic phrase segmentation, Japanese ruby word-boundary review, editable SUG parity, MMS evidence, independent ASR review, and visual-fit checks.
- Mutually exclusive rotating-vinyl and real-time-spectrum wide layouts.
- `wide-layout-v5/no-right-panels`: no outer right panel and no compact backplate behind the vinyl; the album card, footer, rotating record, and lower subtitle panel remain.
- Clip-safe spectrum geometry with top and bottom glow clearance.
- Default 1920x1080, 30 fps, `yuv420p`, BT.709 AV1 delivery with AAC-LC 320 kb/s audio.
- MP4 as the default output; MKV only when explicitly selected with a verified FLAC or PCM WAV source.
- Optional complete-output decode diagnostics; ordinary verification uses probes, sampled decoding, frame inspection, and output identities.
- Japanese pronunciation validation modes `optional`, `required`, and `off`, with `optional` as the default.
- Complete-mix pitch shifting through `scripts/pitch_shift_audio.py` with Rubber Band R3 Finer and formant preservation.
- JSON-based album configuration and song-specific display, timing, and ruby decisions.
- Shared single-track one-click rendering with separate preflight/final ASS generation and ASS identity parity.

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

Install `ffmpeg` and `ffprobe` separately. Rubber Band is needed for pitch shifting; Whisper, MMS, and external MSST are optional evidence lanes. Check the target environment with:

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
4. Use MMS, independent ASR, or MSST-derived evidence when the production requires additional timing evidence.
5. Build the current wide composition and regenerate the current rotating vinyl asset when using the vinyl layout.
6. Render an isolated preview, inspect representative frames, and encode the selected MP4 output.
7. Verify media structure and sampled output, then finalize, promote, or package the accepted files.

Example manifest configuration:

```powershell
$env:KARAOKE_ALBUM_MANIFEST = (Resolve-Path .\config\album.json).Path
uv run --no-sync python scripts/karaoke_timing.py --manifest $env:KARAOKE_ALBUM_MANIFEST --allow-partial-manifest
```

## Shared single-track command

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

For spectrum, use `--visual-style spectrum` and omit `--vinyl`; optionally
add `--spectrum-color RRGGBB --progress-color RRGGBB`. Vinyl uses `--vinyl`
as a canonical identity input, rebuilds and validates the current rotating
vinyl in the new output directory, and passes the generated asset to the
renderer. Spectrum does not require, probe, generate, pass, or report vinyl.

The workflow first writes the independent `karaoke-preflight.ass`, then the
final `karaoke.ass` during rendering, and fails when their SHA-256 identities
do not match. Full duration and MP4-only output with AAC-LC audio are the
defaults. `--smoke-duration`, `--lossless-companion`, and `--full-decode` are
explicit opt-ins; default runs do not create MKV or perform full decode.

The album/batch direct renderer remains the current vinyl-only path. Spectrum
support in the shared single-track workflow or preview is not album-renderer
support.

## References

Each reference has matching English and Chinese versions:

| Topic | English | 中文 |
|---|---|---|
| AV1, FFmpeg, MP4/MKV | [av1-420-commands.md](references/av1-420-commands.md) | [av1-420-commands.zh-CN.md](references/av1-420-commands.zh-CN.md) |
| SUG, independent ASR, pitch | [asr-sug-pitch.md](references/asr-sug-pitch.md) | [asr-sug-pitch.zh-CN.md](references/asr-sug-pitch.zh-CN.md) |
| Wide vinyl/spectrum | [wide-visual-templates.md](references/wide-visual-templates.md) | [wide-visual-templates.zh-CN.md](references/wide-visual-templates.zh-CN.md) |
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
| Artwork and rendering | `build_karaoke_wide_artwork.py`, `render_vinyl_karaoke.py`, `render_karaoke_direct_av1_420_album.py`, `render_karaoke_direct_av1_album.py`, `render_karaoke_direct_hevc444_album.py` |
| Japanese workflow | `karaoke_workflow.py`, `run_karaoke_japanese_workflow.py` |
| Media and release | `inspect_karaoke_media.py`, `transcode_karaoke_av1.py`, `finalize_karaoke_release.py`, `karaoke_release_snapshot.py`, `package_karaoke_numbered_archives.py` |
| Pitch shifting | `pitch_shift_audio.py` |

Recursive package files are `karaoke_common/__init__.py`, `karaoke_common/layout.py`, `karaoke_common/pronunciation.py`, `karaoke_japanese/__init__.py`, and `karaoke_japanese/layout.py`.

Repository support tools are `check_sug_compatibility.py`, `check_karaoke_environment.py`, `install_strangeutagame_integration.py`, `open_editable_project_with_audio_probe.py`, and the standalone mirror of `pitch_shift_audio.py`.

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
