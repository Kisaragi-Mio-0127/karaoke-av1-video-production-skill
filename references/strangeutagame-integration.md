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

Song-specific display, ruby-group, and timing-reading decisions can be supplied through `KARAOKE_DISPLAY_OVERRIDES`, `KARAOKE_RUBY_GROUP_OVERRIDES`, and `KARAOKE_TIMING_READING_OVERRIDES`. The Japanese workflow exposes pronunciation modes `optional`, `required`, and `off`; `optional` is the default.

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

The current wide composition is `wide-layout-v5/no-right-panels`: vinyl card `(40,30,340,402)`, footer bottom padding `12`, and lower subtitle panel beginning at `y=576`. The outer right panel and compact vinyl backplate are absent. The spectrum variant uses clip-safe region `(736,226,1168,348)` with 64 px horizontal glow clearance, 56 px vertical glow clearance, and 8 px bar clearance at the top and bottom.

## Installed files

The public workflow entry is `scripts/run_karaoke_japanese_workflow.py`, coordinated by `scripts/karaoke_workflow.py`. Shared code lives under `karaoke_common/`, while Japanese layout code lives under `karaoke_japanese/`.

The compatibility checker remains in the Skill repository. Run it and the environment checker with the target checkout's project-local Python:

```powershell
Set-Location D:\path\to\StrangeUtaGame
uv run --no-sync python D:\path\to\skill\scripts\check_karaoke_environment.py --target .
uv run --no-sync python D:\path\to\skill\scripts\check_sug_compatibility.py --repo . --project D:\path\to\project.sug
```
