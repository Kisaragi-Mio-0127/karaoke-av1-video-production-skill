# StrangeUtaGame Integration

[简体中文](strangeutagame-integration.zh-CN.md) | English

This reference covers the public Japanese/general integration for a compatible
StrangeUtaGame checkout. It documents the installer, the no-active-network
environment check, and the explicit bootstrap boundary. The public bundle does
not add Chinese/English workflow entries.

## Install

Preview the copy plan, then install into an existing checkout:

```powershell
$skillRoot = (Resolve-Path .).Path
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target <StrangeUtaGame> --dry-run
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target <StrangeUtaGame> --force
```

The installer checks the project layout and copies only paths authorized by
`integration/strangeutagame/dependency-manifest.json`. A differing destination
requires `--force` and receives a rollback backup. The installer does not
create or replace the target Python environment.

## Runtime selection

Use the target checkout's one `.venv` through `uv run --no-sync`. The public
runtime convention is `--device auto` so the workflow follows the CUDA/CPU
capability selected during bootstrap. Pin the backend explicitly with
`--device cuda` or `--device cpu` when the target policy requires it.

Production commands use project-owned `models/mms/model.pt` and
`models/whisper`. They do not implicitly download model files. Prepare missing
runtime inputs with the explicit bootstrap below; do not turn a production
render into an installer.

The public runtime is Japanese/general only. Use the separate local
Chinese/English Skill for those language workflows.

## No-active-network check

`scripts/check_karaoke_environment.py` checks local state without actively
initiating network requests. This is not an operating-system network-isolation
guarantee: it probes local commands, the target `.venv`, NVIDIA/CPU capability,
Python modules, and project-owned model files.

Run it from the public Skill repository:

```powershell
python scripts/check_karaoke_environment.py --target <StrangeUtaGame>
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --deep-verify
```

The built-in bootstrap manifest is used by default. A non-built-in
`--manifest <custom-manifest>` requires `--allow-custom-manifest`; custom model
URLs are still restricted to the built-in HTTPS host allowlist. The default
check verifies configured model sizes only, so it does not read every large
model file. `--deep-verify` reads each complete model and verifies SHA-256.
Use `--redact-paths` when sharing JSON reports outside the local machine.

A failed `core_ok` can still identify a usable Python/model environment when
external tools are absent. Install `git`, `uv`, `ffmpeg`, and `ffprobe`
separately; the bootstrap does not manage them or GPU drivers.

## Explicit bootstrap

Bootstrap is an explicit setup command. It probes NVIDIA/CPU, reuses or
creates the single `<target>/.venv`, installs the version-pinned Python
packages, and downloads missing configured MMS/Whisper files into
`<target>/models/`. It does not create a second environment.

Review the plan first:

```powershell
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --dry-run
```

`--dry-run` deep-verifies existing models and plans actions, but does not write
or actively initiate network requests. When setup is approved, run:

```powershell
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --accept-mms-cc-by-nc-4-0
```

The MMS flag is mandatory before a missing MMS checkpoint may be downloaded. It
acknowledges CC BY-NC 4.0 attribution and non-commercial-use requirements; the
download writes a source/license sidecar beside the checkpoint. A custom
manifest also requires `--allow-custom-manifest`. If no suitable local Python
is available, add `--allow-python-download` to explicitly allow uv to download
a managed interpreter. The default is to refuse that Python download.

Use `--offline` only when required packages and models are already available in
local caches. It blocks model and Python downloads and passes uv offline mode.
Bootstrap never installs or updates `git`, `uv`, `ffmpeg`, `ffprobe`, or GPU
drivers.

## Project configuration

Use an authorized manifest and frozen lyric source. The selected track, audio,
fonts, model paths, and new private output directory must exist before
production starts. Keep canonical SUG, frozen lyrics, private evidence,
companion SUG, and delivery media separate.

The default Japanese route uses project-owned MMS and Whisper paths. Explicit
path overrides are available in the production CLI; they do not authorize
network downloads. Pronunciation validation remains optional. The Japanese
staged, direct, and batch CLIs expose
`--pronunciation-validation {off,optional,required}`, with `optional` as the
default; full-auto does not require the sidecar.

## Production order

The public Japanese production order is:

```text
manifest + song-id + frozen lyric source + new output directory
-> MSST -> private initial SUG -> Japanese MMS
-> editable companion SUG -> current layout -> AV1 MP4
```

Every full-auto or staged run needs a new private output directory. Follow the
installed command's `--help` output for the authoritative option set. The
public runtime convention is `--device auto`; pass `--device cuda` or
`--device cpu` only as an explicit override.

## Full-auto Japanese entry

Run the normal first command from the StrangeUtaGame project root:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py --manifest <manifest> --song-id <song-id> --source <frozen-lyrics.json> --output-dir .render-work/<new-run-dir> --device auto
```

The command prepares MSST vocals, creates a private initial SUG, runs Japanese
MMS, creates an editable companion, prepares the current layout, and renders
AV1 MP4. Its default quality policy is `auto-fallback` and its default visual
style is `spectrum`. Low-confidence fallback evidence remains in the report;
manual or Agent timing adjustment is optional.

## Staged Japanese MMS entry

Use the staged wrapper for audit, recovery, or stage inspection:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py --manifest <manifest> --song-id <song-id> --source <frozen-lyrics.json> --mms-model-path models/mms/model.pt --quality-policy auto-fallback --output-dir <new-private-output-dir> --visual-style spectrum --device auto
```

The required options are `--manifest`, `--song-id`, and a new
`--output-dir`. `--source`, `--sug`, and `--vocals-root` are optional overrides;
without them, project manifest defaults resolve the selected inputs. The
wrapper keeps audit, build, companion, and render artifacts separate. It does
not replace the canonical SUG or silently download a missing MMS checkpoint.

The separate `--allow-mms-network` help option is not a substitute for
bootstrap and is not needed for the public local-model contract.

## Existing SUG rerender and batch

For an existing adjusted or reviewed SUG, use the direct rerender entry. It
does not run MSST or MMS:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py --sug <adjusted-project.sug> --audio <post-mix-audio> --output-dir <new-output-dir> --title <title> --artist <artist> --album-title <album-title> --album-artist <album-artist> --visual-style spectrum
```

For batch rendering from reviewed timing:

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py --manifest <manifest> --visual-style spectrum
```

The batch entry never invokes MMS or creates timing overrides. Validate any
existing timing evidence before promotion.

## Layout and delivery

The current wide composition, spectrum/vinyl choices, and secondary-overlay
rules are defined only in
[wide-visual-templates.md](wide-visual-templates.md). Keep this integration
reference high level and do not duplicate geometry constants.

The default delivery is an AV1 `yuv420p` MP4 with hard subtitles and AAC-LC.
MKV/FLAC and full null decode are explicit opt-ins. Apply the subtitle, stream,
duration, and representative-frame gates before promotion; see
[av1-420-commands.md](av1-420-commands.md) and
[batch-release-gates.md](batch-release-gates.md).

## Installed files and validation

The installed Japanese/general bundle contains the authorized scripts,
language-neutral shared modules, package files, requirements, and support
tools listed by `dependency-manifest.json`. It does not install the public
repository's tests into StrangeUtaGame.

Run compatibility and environment checks with the actual target:

```powershell
Set-Location <StrangeUtaGame>
uv run --no-sync python D:\path\to\skill\scripts/check_sug_compatibility.py --repo . --project <project.sug>
python D:\path\to\skill\scripts/check_karaoke_environment.py --target .
```

The environment check may return non-zero when `ffmpeg` or `ffprobe` is absent;
that result is a diagnostic, not permission for production to download those
tools.
