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

The installer checks the project layout and copies only paths listed under
`scripts`, `shared_modules`, and `package_files` in
`integration/strangeutagame/dependency-manifest.json`. A differing destination
file requires an explicit overwrite decision and is backed up for rollback.

## Environment

Python 3.12 is the tested baseline; the public scripts require Python 3.10 or newer. Reuse the checkout's existing `.venv` and run ordinary commands through `uv run --no-sync`:

```powershell
Set-Location D:\path\to\StrangeUtaGame
if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
  uv sync
}
uv run --no-sync python --version
```

Install `ffmpeg` and `ffprobe` separately and verify libass plus an available
AV1 encoder. Rubber Band is required only for pitch shifting. CJK fonts are
selected according to the production configuration. The Japanese full-auto
entry also needs the project-local MSST preparation and MMS model inputs; the
staged Japanese MMS wrapper uses the same local evidence. Use the existing
project environment rather than downloading models during a run.

## Project configuration

Copy `examples/album.example.json`, replace its placeholders, and pass the resulting manifest through `--manifest` or `KARAOKE_ALBUM_MANIFEST`.

Song-specific display, ruby-group, and timing-reading decisions can be supplied through `KARAOKE_DISPLAY_OVERRIDES`, `KARAOKE_RUBY_GROUP_OVERRIDES`, and `KARAOKE_TIMING_READING_OVERRIDES`. The Japanese workflow exposes pronunciation modes `optional`, `required`, and `off`; non-blocking `optional` is the default.

## Production order

```text
manifest + song-id + frozen lyric source + new output directory
-> MSST -> private initial SUG -> Japanese MMS
-> editable companion SUG -> current layout -> AV1 MP4
```

The public Japanese one-command entry is
`scripts/run_karaoke_japanese_full_auto.py`. Its default quality policy is
`auto-fallback`, so manual or Agent timing alignment is optional after the
companion SUG is created. The existing
`scripts/run_karaoke_japanese_workflow.py` remains the direct rerender entry
for an already adjusted or reviewed SUG; it does not run MMS.

## Staged Japanese MMS entry

When the validated bundle includes `scripts/run_karaoke_japanese_mms_workflow.py`,
use it as the lower-level stage/recovery entry for Japanese timing. Its
required arguments are `--manifest`, `--song-id`, and a new, non-existent
`--output-dir`:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <existing-manifest> --song-id <song-id> `
  --mms-model-path <project-mms-model> `
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

The staged wrapper keeps audit, build, companion, and render artifacts
separate. Use it for stage-by-stage handling, recovery, or gate inspection; a
failed gate leaves review artifacts without replacing a release video.

The canonical editable timing source is the `.sug` project. Candidate generation fills missing ruby spans, review writes accepted corrections to the canonical SUG, and rendering reads that reviewed project without inferring new ruby.

When pitch shifting is requested, run `scripts/pitch_shift_audio.py` on the complete mix before timing and rendering. The verified shifted audio becomes the selected source for evidence, preview, and muxing.

## Visual contract

The current wide composition, vinyl/spectrum choices, and secondary-overlay
rules are defined only in
[`wide-visual-templates.md`](wide-visual-templates.md). Keep this integration
guide linked to that single source; this guide intentionally keeps layout
details high level.

The full-auto route prepares the selected visual style and keeps its generated
artwork with the run. Review representative frames before accepting the
composition.

The supported direct album entry is `render_karaoke_direct_av1_420_album.py`.
It uses `karaoke_direct_album_planning.py` for manifest selection and task
planning. The retired HEVC 4:4:4 entry is not installed.

Batch rendering never invokes MMS or creates timing overrides. If the fixed-path `timing_overrides` artifact already exists, the batch entry automatically consumes its existing `visual_release_overrides_ms` and records the artifact identity. The renderer consumes the artifact but does not validate MMS provenance, so validate its source, generation identity, review status, and Japanese workflow gate before starting the batch.

## Installed files

The public Japanese automation entry is
`scripts/run_karaoke_japanese_full_auto.py`. The existing
`scripts/run_karaoke_japanese_workflow.py` is the direct SUG rerender entry,
and `scripts/run_karaoke_japanese_mms_workflow.py` is the staged MMS/recovery
entry. Shared code lives under `karaoke_common/`, while Japanese layout code
lives under `karaoke_japanese/`.

The compatibility checker remains in the Skill repository. Run it and the environment checker with the target checkout's project-local Python:

```powershell
Set-Location <StrangeUtaGame>
uv run --no-sync python D:\path\to\skill\scripts\check_karaoke_environment.py --target .
uv run --no-sync python <skill>\scripts\check_sug_compatibility.py --repo . --project <project.sug>
```
