# StrangeUtaGame Integration

[简体中文](strangeutagame-integration.zh-CN.md) | English

This bundle adds the karaoke production workflow to a compatible StrangeUtaGame checkout. The dependency manifest defines the installed scripts, shared modules, packages, and support tools.

## Install

Preview the copy plan, then install:

```powershell
$skillRoot = (Resolve-Path .).Path
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target D:\path\to\StrangeUtaGame --dry-run
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target D:\path\to\StrangeUtaGame
```

The installer checks `pyproject.toml`, `src/strange_uta_game`, and `scripts`. It copies only paths listed under `scripts`, `shared_modules`, and `package_files` in `integration/strangeutagame/dependency-manifest.json`. A differing destination file requires an explicit overwrite decision and is backed up under `.karaoke-skill-backup/<UTC stamp>/`.

## Environment

Python 3.12 is the tested baseline; the public scripts require Python 3.10 or newer. Reuse the checkout's existing `.venv` and run ordinary commands through `uv run --no-sync`:

```powershell
Set-Location D:\path\to\StrangeUtaGame
if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
  uv sync
}
uv run --no-sync python --version
```

Install `ffmpeg` and `ffprobe` separately and verify libass plus an available AV1 encoder. Rubber Band is required only for pitch shifting. Whisper, MMS, MSST, and CJK fonts are selected according to the production configuration.

The tested compatibility baseline is StrangeUtaGame 1.4.5 with SUG storage format 0.3.0. Read the application version from `src/strange_uta_game/__version__.py` and the storage format from `SugMigrator.CURRENT_VERSION`.

## Project configuration

Copy `examples/album.example.json`, replace its placeholders, and pass the resulting manifest through `--manifest` or `KARAOKE_ALBUM_MANIFEST`.

Song-specific display, ruby-group, and timing-reading decisions can be supplied through `KARAOKE_DISPLAY_OVERRIDES`, `KARAOKE_RUBY_GROUP_OVERRIDES`, and `KARAOKE_TIMING_READING_OVERRIDES`. The Japanese workflow exposes pronunciation modes `optional`, `required`, and `off`; non-blocking `optional` is the default. For multi-singer identity and top-overlay rules, read [singer-overlays.md](singer-overlays.md).

## Production order

```text
manifest -> Japanese workflow -> optional MSST evidence -> ASR/MMS review
-> source lyrics -> candidate ruby in canonical SUG -> contextual ruby review
-> timing and phrase decisions -> read-only renderer -> ASS/report/frames
-> composition -> AV1 render -> media inspection -> finalization -> archive
```

The canonical editable timing source is the `.sug` project. Candidate generation fills missing ruby spans, review writes accepted corrections to the canonical SUG, and rendering reads that reviewed project without inferring new ruby.

When pitch shifting is requested, run `scripts/pitch_shift_audio.py` on the complete mix before timing and rendering. The verified shifted audio becomes the selected source for evidence, preview, and muxing.

## Visual contract

The vinyl remains rotating and is regenerated for formal and test runs with `direction-neutral-concentric-grooves/v3/backplate-absent`.

The current wide composition is `wide-layout-v6/top-secondary-clearance`: vinyl card `(40,30,340,402)`, footer bottom padding `12`, and lower subtitle panel beginning at `y=576`. The outer right panel and compact vinyl backplate are absent. The spectrum variant uses clip-safe region `(736,226,1168,348)` with 64 px horizontal glow clearance, 56 px vertical glow clearance, and 8 px bar clearance at the top and bottom. The secondary overlay uses anchor `y=12`, default `60 px`, minimum `36 px`, content safe band `y=0..96`, and outline/glow reserve through `y=107`; title label/title/artist positions are `y=120/155/220`, using actual ink bounds with at least `16 px` clearance from the reserve.

Direct album entry points are codec-specific: `render_karaoke_direct_av1_420_album.py` is the AV1 4:2:0 command and `render_karaoke_direct_hevc444_album.py` is the HEVC 4:4:4 command. The older `render_karaoke_direct_av1_album.py` name remains a deprecated compatibility entry for HEVC. Both codec lanes use the neutral `karaoke_direct_album_planning.py` module for manifest selection and task planning.

## AV1 4:2:0 batch workflow

The AV1 4:2:0 batch entry accepts `--visual-style vinyl|spectrum|both` and
defaults to `vinyl`:

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <album-manifest> `
  --visual-style <vinyl|spectrum|both>
```

`spectrum` does not require, probe, generate, pass, or report a vinyl asset.
`both` creates separate vinyl and spectrum AV1 4:2:0 artifacts, each with
its own media and report identity. Both variants for one song/profile share
a hash-identical profile ASS and publish serially. They are two independent
products, not one output containing both effects. `--single-track` selects exactly one
song and one profile; with `--visual-style both`, that selection can produce
two style variants.

`--lossless-companion` and `--full-decode` remain explicit opt-ins for the
selected style or styles. Neither option is implied by `both`.

## Shared single-track workflow

The shared one-click entry is `scripts/run_karaoke_japanese_workflow.py`.
`--visual-style vinyl|spectrum` defaults to `vinyl`, and every run requires a
new, non-existent `--output-dir`:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --composition <composition-png> --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style vinyl --vinyl <canonical-vinyl-png>
```

For spectrum, use `--visual-style spectrum` and omit `--vinyl`; the optional
`--spectrum-color RRGGBB` and `--progress-color RRGGBB` flags are valid only
there. Vinyl uses `--vinyl` as an identity input, rebuilds and validates the
current rotating asset inside the new output directory, and passes that
generated asset to rendering. Spectrum does not require, probe, generate,
pass, or report vinyl.

The workflow writes an independent `karaoke-preflight.ass` first and the
final `karaoke.ass` during MP4 rendering, then requires their SHA-256
identities to match. Full duration and MP4-only output are defaults;
`--lossless-companion` and `--full-decode` are explicit opt-ins for MKV and
full-decode diagnostics. Japanese pronunciation validation defaults to
non-blocking `optional`. The one-click route and the underlying renderer share the same
singer, overlay, ruby, container, and diagnostic gates. The album/batch direct
renderer follows the AV1 4:2:0 batch contract above; keep each style's output
and validation identity separate.

## Installed files

The public workflow entry is `scripts/run_karaoke_japanese_workflow.py`, coordinated by `scripts/karaoke_workflow.py`. Shared code lives under `karaoke_common/`, while Japanese layout code lives under `karaoke_japanese/`.

The compatibility checker remains in the Skill repository. Run it and the environment checker with the target checkout's project-local Python:

```powershell
Set-Location D:\path\to\StrangeUtaGame
uv run --no-sync python D:\path\to\skill\scripts\check_karaoke_environment.py --target .
uv run --no-sync python D:\path\to\skill\scripts\check_sug_compatibility.py --repo . --project D:\path\to\project.sug
```
