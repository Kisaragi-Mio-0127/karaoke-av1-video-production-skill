# Karaoke AV1 Video Production Skill

[中文说明](README.zh-CN.md)

This repository packages a reusable Codex Skill and a guarded StrangeUtaGame
integration for Japanese karaoke timing and AV1 video production. The public
bundle is Japanese and language-neutral only; Chinese/English workflows stay
in the separate local Skill. Track-specific data remains in external manifests
and frozen lyric sources.

## What is automatic

The recommended Japanese entry is the single-command
`scripts/run_karaoke_japanese_full_auto.py`. Given a manifest, song ID, frozen
lyrics, and a new output directory, it:

- prepares the selected MSST vocal stem;
- builds a private initial SUG;
- runs Japanese MMS and creates an editable companion SUG;
- prepares the current layout and renders the AV1 MP4 delivery.

The default quality policy is `auto-fallback`. Usable high-confidence MMS
timing is applied, while low-confidence or unresolved units retain canonical
timing and remain visible in the report. Manual or Agent timing adjustment is
optional after the companion SUG exists; it is not a prerequisite for the
automated run.

The existing `scripts/run_karaoke_japanese_workflow.py` has a different role:
it directly rerenders an existing adjusted or reviewed SUG and does not run
MSST or MMS. The lower-level
`scripts/run_karaoke_japanese_mms_workflow.py` is for staged audit, recovery,
and gate inspection.

## Runtime and model boundary

Run production commands from the StrangeUtaGame project root with its existing
`.venv`:

```powershell
Set-Location <StrangeUtaGame>
uv run --no-sync python --version
```

The public runtime follows bootstrap hardware detection with `--device auto`.
Override it explicitly with `--device cuda` or `--device cpu` when a fixed
backend is required. Production commands use project-owned
`models/mms/model.pt` and `models/whisper` and do not implicitly download
models. Missing inputs fail closed; prepare the environment separately.

The public environment tools have distinct boundaries:

1. `check_karaoke_environment.py` does not actively initiate network requests.
   It probes local commands, the target `.venv`, the selected CUDA/CPU backend,
   and project-owned model files. It checks exact model sizes by default;
   `--deep-verify` is the optional full-file SHA-256 check. A custom manifest
   requires `--allow-custom-manifest`; `--redact-paths` is available when the
   JSON report must not contain absolute local paths.
2. `bootstrap_karaoke_environment.py` performs setup only when explicitly
   invoked. It probes NVIDIA/CPU, reuses or creates the one `target/.venv`,
   installs the version-pinned Python packages, and downloads missing MMS/Whisper
   files into `target/models/`. A custom manifest requires
   `--allow-custom-manifest`. MMS model download requires
   `--accept-mms-cc-by-nc-4-0`, which acknowledges required attribution and
   non-commercial use. A managed Python download requires
   `--allow-python-download`.
3. Bootstrap does not manage `git`, `uv`, `ffmpeg`, `ffprobe`, or GPU drivers.
   Install and maintain those separately. `--dry-run` deep-verifies and plans
   without writing or actively initiating network requests; `--offline` blocks
   model and Python downloads and passes uv offline mode.

Check an existing target without downloading or changing it:

```powershell
python scripts/check_karaoke_environment.py --target <StrangeUtaGame>
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --deep-verify
```

Review the plan, then explicitly bootstrap when setup is wanted:

```powershell
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --dry-run
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> `
  --accept-mms-cc-by-nc-4-0
```

Use `--offline` on the explicit bootstrap command when all required packages
and models are already available locally. If a custom manifest is used, add
`--allow-custom-manifest`; if a managed Python must be downloaded, add
`--allow-python-download`. The bootstrap manifest selects CUDA-oriented Torch
packages when `nvidia-smi` detects NVIDIA hardware and the official CPU index
otherwise. This selection does not install a driver. The public production
runtime still follows `--device auto` unless `--device cuda` or
`--device cpu` is supplied.

## Install the integration

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

The installer copies only paths authorized by
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

Japanese staged MMS/recovery:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> `
  --mms-model-path models/mms/model.pt `
  --quality-policy auto-fallback --output-dir <new-private-output-dir> `
  --visual-style spectrum
```

The staged wrapper accepts optional `--source`, `--sug`, and `--vocals-root`
overrides. It does not download a missing MMS checkpoint during production.

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
  --manifest <manifest> --visual-style spectrum
```

Use a new output directory for every full-auto or staged run. In the public
runtime, use `--device auto` to follow the bootstrap probe, or explicitly pass
`--device cuda`/`--device cpu` to pin the backend. The exact option set is
authoritative in each command's `--help` output. Pronunciation
validation remains optional: the staged, direct, and batch Japanese CLIs
expose `--pronunciation-validation {off,optional,required}` with `optional` as
the default; full-auto does not require this sidecar.

## Layout and delivery

The full-auto route prepares the current wide layout automatically. Choose
`spectrum` for the default spectrum presentation or `vinyl` for a record
visual. Geometry belongs to the single source of truth in
[wide-visual-templates.md](references/wide-visual-templates.md).

The default delivery is an MP4 with AV1 video, hard subtitles, and AAC-LC
audio. Other containers and full-decode diagnostics are explicit opt-ins and
must be verified before promotion. See
[batch-release-gates.md](references/batch-release-gates.md) and
[av1-420-commands.md](references/av1-420-commands.md).

## Repository layout

- `SKILL.md`: concise route selection and release contract.
- `references/`: detailed workflow, timing, integration, and media guidance.
- `integration/strangeutagame/`: installable Japanese and generic support files.
- `scripts/`: installer, environment check, and explicit bootstrap tools.
- `tests/`: repository and integration regression tests; not installed into
  StrangeUtaGame.

## Validation

The Skill repository does not create a second project environment. Reuse the
target checkout's `.venv` for repository checks:

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
