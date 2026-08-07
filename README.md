# Karaoke AV1 Video Production Skill

[中文说明](README.zh-CN.md)

This repository packages a reusable Codex Skill and a guarded
StrangeUtaGame integration for Japanese karaoke timing and AV1 video
production. The public integration contains generic and Japanese workflow
code only; track-specific data stays in external manifests and review JSON.

## What is automatic

- The normal Japanese entry consumes an existing SUG and audio file, builds
  the current composition, and renders the final MP4.
- The Japanese MMS entry audits timing, writes a separate editable companion
  SUG, builds the current composition, and renders the final MP4. Use
  `--quality-policy auto-fallback` when the run must finish without manual
  timing review while preserving unresolved evidence in its report.
- The timing builder can create canonical timing deliverables from a manifest
  and frozen lyric source before either render route is used.

Automatic does not mean input-free. The selected route still needs its local
manifest, authorized audio and frozen lyrics, fonts, and the required model or
stem inputs. It never replaces frozen lyrics with an unreviewed transcription.

## Fixed generated layout

One-click workflows generate `wide-layout-v7/cover-palette` inside every new
output directory. `vinyl` creates a fresh record asset for that run;
`spectrum` creates no vinyl asset. Explicit composition files are advanced
compatibility overrides and must pass the same layout gates.

Detailed geometry lives only in
[wide-visual-templates.md](references/wide-visual-templates.md).

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

Normal Japanese video from an existing SUG:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <project.sug> --audio <audio> --output-dir <new-output> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style spectrum
```

Japanese MMS timing companion and video without manual timing intervention:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> `
  --mms-model-path models/mms/model.pt `
  --quality-policy auto-fallback `
  --output-dir <new-output> --visual-style spectrum
```

Batch AV1 4:2:0 rendering from reviewed timing:

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <manifest> --visual-style <vinyl-or-spectrum>
```

MP4 is the default. MKV and full null decoding are created or run only after
their explicit flags are supplied. Lyrics and cover network access also
require their separate explicit authorization flags.

## Repository layout

- `SKILL.md`: concise routing and release contract.
- `references/`: detailed workflow, layout, timing, and media gates.
- `integration/strangeutagame/`: installable generic and Japanese scripts.
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
