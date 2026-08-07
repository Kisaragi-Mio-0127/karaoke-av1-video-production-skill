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

Install `ffmpeg` and `ffprobe` separately and verify libass plus an available AV1 encoder. Rubber Band is required only for pitch shifting. CJK fonts are selected according to the production configuration. Independent ASR/Whisper and MSST are optional evidence lanes. MMS is not an installation or runtime dependency for the one-click or batch entry. The only documented MMS entry is the separate Japanese-only `run_karaoke_japanese_mms_workflow.py`; install or configure its model only for that explicit contract.

The tested compatibility baseline is StrangeUtaGame 1.4.5 with SUG storage format 0.3.0. Read the application version from `src/strange_uta_game/__version__.py` and the storage format from `SugMigrator.CURRENT_VERSION`.

## Project configuration

Copy `examples/album.example.json`, replace its placeholders, and pass the resulting manifest through `--manifest` or `KARAOKE_ALBUM_MANIFEST`.

Song-specific display, ruby-group, and timing-reading decisions can be supplied through `KARAOKE_DISPLAY_OVERRIDES`, `KARAOKE_RUBY_GROUP_OVERRIDES`, and `KARAOKE_TIMING_READING_OVERRIDES`. The Japanese workflow exposes pronunciation modes `optional`, `required`, and `off`; non-blocking `optional` is the default.

## Production order

```text
manifest -> one-click Japanese workflow (no MMS)
-> source lyrics -> candidate ruby in canonical SUG -> contextual ruby review
-> timing and phrase decisions -> read-only renderer -> ASS/report/frames
-> composition -> AV1 render -> media inspection -> finalization -> archive
```

The one-click interface exposes no MMS option and never invokes MMS. Independent ASR and MSST are separate optional evidence lanes. The dedicated Japanese MMS entry is not a default `ASR/MMS review` step and must remain outside the one-click and batch commands.

## Explicit Japanese MMS entry

When the validated bundle includes `scripts/run_karaoke_japanese_mms_workflow.py`,
use it only for an explicitly requested Japanese timing workflow. It is not a
generic language adapter and must never be called for `zh` or `en`. Its required
arguments are `--manifest`, `--song-id`, and a new, non-existent
`--output-dir`:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <existing-manifest> --song-id <song-id> `
  --mms-model-path models/mms/model.pt `
  --quality-policy auto-fallback --output-dir <new-output-dir> `
  --visual-style spectrum
```

The current composition is generated inside the output by default. An explicit
`--composition` is an advanced gated compatibility override. The manifest
selection resolves the mix and canonical reviewed SUG. Frozen
lyrics and the matching MSST `Vocals.wav` resolve from project defaults unless
`--source` or `--vocals-root` overrides them. It accepts no separate SUG-path
argument. Its optional arguments also cover visual, font, cover, and render policy.
Vinyl generates a new record asset for the current run; spectrum creates none.

The contract is local-first. `--mms-model-path models/mms/model.pt` explicitly
selects the project-owned checkpoint. `.cache` is reserved for derived runtime
data and evidence, not model authority, and no model-download fallback is part
of this contract. Keep resolved-model and cache provenance separate in reports.

The new output directory contains only `audit/`, `build/`, and `render/`. Gate
audit before build and build before render. Only reviewed
`visual_release_overrides_ms` from `build/timing_overrides.json` enter render;
audit data, other build values, and separated vocals are never delivery tracks.
If a gate fails, keep review artifacts without producing or replacing a release
video. If the entry is absent, do not reconstruct it by adding MMS flags to
another workflow.

The canonical editable timing source is the `.sug` project. Candidate generation fills missing ruby spans, review writes accepted corrections to the canonical SUG, and rendering reads that reviewed project without inferring new ruby.

When pitch shifting is requested, run `scripts/pitch_shift_audio.py` on the complete mix before timing and rendering. The verified shifted audio becomes the selected source for evidence, preview, and muxing.

## Visual contract

The vinyl remains rotating and is regenerated for formal and test runs with `direction-neutral-concentric-grooves/v3/backplate-absent`.

The current wide composition, vinyl/spectrum geometry, secondary-overlay rules,
and all numeric layout constants are defined only in
[`wide-visual-templates.md`](wide-visual-templates.md). Keep this integration
guide linked to that single source; do not copy coordinates or typography
constants here.

The shared cover-palette extractor filters near-black pixels by absolute chroma before Lab-neighbourhood area aggregation, emits an ordered deterministic eight-colour palette, and records the cover and extractor identities. Review the selected colours in representative frames before accepting the composition.

The supported direct album entry is `render_karaoke_direct_av1_420_album.py`.
It uses `karaoke_direct_album_planning.py` for manifest selection and task
planning. The retired HEVC 4:4:4 entry is not installed.

Batch rendering never invokes MMS or creates timing overrides. If the fixed-path `timing_overrides` artifact already exists, the batch entry automatically consumes its existing `visual_release_overrides_ms` and records the artifact identity. The renderer consumes the artifact but does not validate MMS provenance, so validate its source, generation identity, review status, and Japanese workflow gate before starting the batch.

## Installed files

The public default workflow entry is `scripts/run_karaoke_japanese_workflow.py`, coordinated by `scripts/karaoke_workflow.py`. A validated bundle may additionally expose the separate Japanese-only `scripts/run_karaoke_japanese_mms_workflow.py`; its absence must not be worked around by changing the default entry. Shared code lives under `karaoke_common/`, while Japanese layout code lives under `karaoke_japanese/`.

The compatibility checker remains in the Skill repository. Run it and the environment checker with the target checkout's project-local Python:

```powershell
Set-Location D:\path\to\StrangeUtaGame
uv run --no-sync python D:\path\to\skill\scripts\check_karaoke_environment.py --target .
uv run --no-sync python D:\path\to\skill\scripts\check_sug_compatibility.py --repo . --project D:\path\to\project.sug
```
