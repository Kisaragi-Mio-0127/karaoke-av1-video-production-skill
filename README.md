# Karaoke AV1 Video Production Skill

[中文说明](README.zh-CN.md)

This repository packages a reusable Codex Skill and a guarded
StrangeUtaGame integration for Japanese karaoke timing and AV1 video
production. The public integration contains Japanese and language-neutral
workflow files only; track-specific data stays in external manifests and
frozen lyric sources.

## What is automatic

The recommended Japanese entry is the single-command
`scripts/run_karaoke_japanese_full_auto.py`. Given a manifest, a song ID, a
frozen lyric source, and a new output directory, it automatically:

- prepares the selected MSST vocal stem;
- builds a private initial SUG;
- runs the Japanese MMS workflow and creates an editable companion SUG;
- prepares the current layout and renders the AV1 MP4 delivery.

`auto-fallback` is the default quality policy. The run can complete without
manual timing alignment: usable high-confidence MMS timing is applied, while
low-confidence or unresolved units retain canonical timing and remain visible
in the report. Manual or Agent timing alignment is an optional follow-up using
the companion SUG; it is not required for the automated run.

The existing `scripts/run_karaoke_japanese_workflow.py` has a different role:
it takes an existing manually adjusted or reviewed SUG and directly rerenders
it. It does not create the private initial SUG or run MMS.

The lower-level `scripts/run_karaoke_japanese_mms_workflow.py` is the staged
MMS/recovery entry. Use it when a run needs stage-by-stage audit, build,
companion, or render handling, or when its artifacts need inspection. It is
not the normal first command for a new Japanese track.

Automatic does not mean input-free. The selected track still needs an
authorized local audio source, a manifest, frozen lyrics, fonts, and the
project-owned model or stem inputs. The workflow never replaces frozen lyrics
with an unreviewed transcription.

## Generated layout and delivery

The full-auto route prepares the current wide layout automatically. Choose
`spectrum` for the default spectrum presentation or `vinyl` when a record
visual is wanted; the run keeps generated layout assets with that run.
Detailed geometry belongs to
[wide-visual-templates.md](references/wide-visual-templates.md).

The default delivery is an MP4 with AV1 video, hard subtitles, and AAC-LC
audio. Other containers or diagnostics are opt-in and must be verified before
promotion.

## Install

Clone the Skill into the Codex skills directory:

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git `
  "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

Install the bundled integration into an existing StrangeUtaGame checkout.
Review the dry run before allowing replacements:

```powershell
python scripts/install_strangeutagame_integration.py --target <project> --dry-run
python scripts/install_strangeutagame_integration.py --target <project> --force
```

The installer copies only files authorized by
[`dependency-manifest.json`](integration/strangeutagame/dependency-manifest.json)
and keeps rollback backups for replaced files.

## Main commands

Japanese full-auto production from a manifest track:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py `
  --manifest <manifest> `
  --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --output-dir <new-private-output-dir> `
  --quality-policy auto-fallback
```

Japanese video rerender from an existing adjusted SUG:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <adjusted-project.sug> --audio <post-mix-audio> `
  --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style spectrum
```

Generic batch AV1 4:2:0 rendering from reviewed timing:

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <manifest> --visual-style <vinyl-or-spectrum>
```

Use a new output directory for each one-command run. The full-auto command
uses the project defaults for model and stem locations; explicit overrides are
available when the project configuration requires them.

## Repository layout

- `SKILL.md`: concise route selection and release contract.
- `references/`: detailed workflow, timing, integration, and media guidance.
- `integration/strangeutagame/`: installable generic and Japanese support files.
- `scripts/`: installer and local Skill support tools.
- `tests/`: repository and integration regression tests; not installed into
  StrangeUtaGame.

## Validation

Reuse the existing StrangeUtaGame uv environment; the Skill repository does
not create a second virtual environment:

```powershell
$project = (Resolve-Path <StrangeUtaGame>).Path
uv run --no-sync --project $project python -m pytest -q `
  --basetemp .test-tmp tests
uv run --no-sync --project $project ruff check --config ruff.toml `
  integration/strangeutagame/scripts scripts tests
uv run --no-sync --project $project python scripts/install_strangeutagame_integration.py `
  --target <project> --dry-run
```

Code and documentation are licensed under GPL-3.0-only. Runtime component
notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
