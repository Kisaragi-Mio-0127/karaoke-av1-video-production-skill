# StrangeUtaGame Integration

[简体中文](strangeutagame-integration.zh-CN.md) | English

This reference covers installation, environment preparation, production
entries, workspace dependencies, and validation for a compatible StrangeUtaGame
checkout.

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

Use the target checkout's one `.venv` through `uv run --no-sync`. Use
`--device auto` so the workflow follows the CUDA/CPU
capability selected during bootstrap. Pin the backend explicitly with
`--device cuda` or `--device cpu` when the target policy requires it.

Production commands use project-owned `models/mms/model.pt` and
`models/whisper`. They do not implicitly download model files. Prepare missing
runtime inputs with the explicit bootstrap below; do not turn a production
render into an installer.

`local-mms-fa` is the default alignment backend. The experimental
Japanese-only NextFire backend is available only through
`--mms-backend nextfire-ja-latn`, is not claimed to be better, and resolves
only `models/hf/nextfire-mms-ja-latn`. It never downloads at runtime, uses no
general Hugging Face cache, and executes no remote code.

## FFmpeg and FFprobe

The default supported baseline is a matched FFmpeg/FFprobe 8.x build. The
tested Windows package is Gyan FFmpeg 8.0.1 Essentials. Keep both executables
in the project-owned layout:

```text
<StrangeUtaGame>/tools/ffmpeg/bin/ffmpeg.exe
<StrangeUtaGame>/tools/ffmpeg/bin/ffprobe.exe
```

Download the pinned
[Gyan FFmpeg 8.0.1 Essentials archive](https://github.com/GyanD/codexffmpeg/releases/download/8.0.1/ffmpeg-8.0.1-essentials_build.zip),
extract it, and copy both files from the archive's `bin` directory into the
layout above. Do not use the moving `ffmpeg-release` URL for the default setup,
because it can advance to a new major version. Treat FFmpeg 9.x as an explicit
compatibility migration that requires a successful NVENC probe. Verify from
the target checkout:

```powershell
tools\ffmpeg\bin\ffmpeg.exe -hide_banner -version
tools\ffmpeg\bin\ffprobe.exe -hide_banner -version
tools\ffmpeg\bin\ffmpeg.exe -hide_banner -filters | Select-String 'subtitles|ass'
tools\ffmpeg\bin\ffmpeg.exe -hide_banner -encoders | Select-String 'av1_nvenc|libaom-av1|aac'
```

The shared resolver uses this order: explicit `--ffmpeg`/`--ffprobe`, the
`FFMPEG`/`FFPROBE` environment variables, the project-owned pair, system
`PATH`, then imageio-ffmpeg as an FFmpeg-only compatibility fallback.
imageio-ffmpeg does not supply FFprobe. FFprobe reads container and stream
metadata; it does not render, encode, or modify media.

## No-active-network check

`scripts/check_karaoke_environment.py` checks local state without actively
initiating network requests. This is not an operating-system network-isolation
guarantee: it probes local commands, the target `.venv`, NVIDIA/CPU capability,
Python modules, and project-owned model files.

Run it from the integration repository:

```powershell
python scripts/check_karaoke_environment.py --target <StrangeUtaGame>
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --deep-verify
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --nextfire-mms-ja-latn
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

For the optional NextFire snapshot, review its separate plan, then install it
only with both confirmations:

```powershell
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --nextfire-mms-ja-latn --dry-run
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --nextfire-mms-ja-latn --accept-nextfire-agpl-3-0 --accept-mms-cc-by-nc-4-0
```

The weights remain local and are never committed to this repository.

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

Use an authorized manifest and one explicit lyric source. The selected track, audio,
fonts, model paths, and new output location must be valid before
production starts. The source file must already exist unless an explicit
`--refresh-source` authorizes a single-song refresh. When
`--netease-song-id` is omitted, the command reads a supported embedded song ID.
Use `--lyrics-file <lyrics.lrc|lyrics.txt>` instead of `--source` for manual
UTF-8 lyrics. Plain text receives uniform coarse anchors and requires timing review.
Keep the source SUG, frozen lyrics, working evidence, companion SUG, and
delivery media as separate artifacts.

Run `scripts/karaoke_netease_metadata.py <audio> --identity --fetch-album` only
when an album-detail query is explicitly authorized. It contacts the NetEase
album endpoint and reports album artists separately from the track artists in
the audio.

The default Japanese route uses project-owned MMS and Whisper paths. Explicit
path overrides are available in the production CLI; they do not authorize
network downloads. Pronunciation validation remains optional. The Japanese
staged, direct, and batch CLIs expose
`--pronunciation-validation {off,optional,required}`, with `optional` as the
default; full-auto does not require the sidecar.

## Production order

The Japanese production order is:

```text
manifest + song-id + one lyric source + new output directory
-> MSST -> working initial SUG -> Japanese MMS
-> editable companion SUG -> current layout -> AV1 MP4
-> relocatable editable SUG
```

Every full-auto or staged run needs a new output directory. Follow the
installed command's `--help` output for the authoritative option set. The
runtime convention is `--device auto`; pass `--device cuda` or
`--device cpu` only as an explicit override.

## Full-auto Japanese entry

Run the normal first command from the StrangeUtaGame project root:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py --manifest <manifest> --song-id <song-id> --source <frozen-lyrics.json> --output-dir .render-work/<new-run-dir> --device auto
```

For manual lyrics, replace `--source <frozen-lyrics.json>` with
`--lyrics-file <lyrics.lrc|lyrics.txt>`.

To select the experimental Japanese-only backend, add
`--mms-backend nextfire-ja-latn`. The normal dual-audio audit and
`auto-fallback`/`strict` policy remain in force.

The command prepares MSST vocals, creates a working initial SUG, runs Japanese
MMS, creates an editable companion, prepares the current layout, and renders
AV1 MP4. Its default quality policy is `auto-fallback` and its default visual
style is `spectrum`. Low-confidence fallback evidence remains in the report;
manual or Agent timing adjustment is optional.

Add `--output-mode subtitle-overlay` to full-auto or the staged MMS command to
retain every MMS stage and change the final render only. With no supplied
footage, the result is a silent transparent ProRes 4444 MOV. Add
`--background-video <footage>` for direct FFmpeg AV1/AAC composition; long
footage is trimmed and short footage is followed by black. The background path
probes `av1_nvenc` and automatically retries `libaom-av1` after a hardware
initialization or render failure.

Album display metadata defaults to audio tags, then the track title and artist.
Use `--metadata-source-audio` when the delivery file is transformed or tagless.

## Staged Japanese MMS entry

Use the staged wrapper for audit, recovery, or stage inspection:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py --manifest <manifest> --song-id <song-id> --source <frozen-lyrics.json> --mms-model-path models/mms/model.pt --quality-policy auto-fallback --output-dir <new-output-dir> --visual-style spectrum --device auto
```

Use `--mms-backend nextfire-ja-latn` instead of `--mms-model-path` when the
explicit experimental backend is intended. Its dual-audio audit and quality
policies are unchanged.

The required options are `--manifest`, `--song-id`, and a new
`--output-dir`. `--source`, `--sug`, and `--vocals-root` are optional overrides;
without them, project manifest defaults resolve the selected inputs. The
wrapper keeps audit, build, companion, and render artifacts separate. It does
not replace the canonical SUG or silently download a missing MMS checkpoint.

The separate `--allow-mms-network` help option is not a substitute for
bootstrap and is not needed for the local-model contract.

## Existing SUG rerender and batch

For an existing adjusted or reviewed SUG, use the direct rerender entry. It
does not run MSST or MMS:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py --sug <adjusted-project.sug> --audio <post-mix-audio> --output-dir <new-output-dir> --title <title> --artist <artist> --visual-style spectrum
```

This entry accepts the same `--output-mode subtitle-overlay` and optional
`--background-video` arguments.

Pass album flags only as explicit overrides. Every successful direct or
full-auto render includes `editable-project/<name>.sug` with a verified media
path.

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

## Workspace dependencies and compatibility

The installer copies only the paths listed in
[`dependency-manifest.json`](../integration/strangeutagame/dependency-manifest.json):
production scripts and shared packages go under `<target>/scripts/`, and the
requirements files go in the target root. Installation and production use the
target checkout's `.venv`.

Full-auto, staged MMS, direct rerender, and batch rendering require the target
application runtime, its SUG model, `SugMigrator`, parser/persistence support,
the selected manifest and media resources, fonts, FFmpeg/FFprobe, and the
declared MMS/Whisper models. Media, artwork, packaging, and model-path helpers
may avoid application imports, but still require their documented inputs and
external tools.

`install_strangeutagame_integration.py`,
`check_karaoke_environment.py`, and
`bootstrap_karaoke_environment.py` remain in the integration repository and
operate on a target supplied with `--target`. The compatibility check imports
the target's runtime `__version__`, `SugMigrator`, and `SugProjectParser`, then
reads a representative SUG project without saving it. Use that actual parsing
check together with the installer's exact application-version and SUG-format
checks. Parser success alone does not authorize installation.

## Installed files and validation

The installed integration contains the authorized scripts, shared modules,
package files, requirements, and support tools listed by
[`dependency-manifest.json`](../integration/strangeutagame/dependency-manifest.json).
Repository tests are not installed into StrangeUtaGame.

Run compatibility and environment checks with the actual target:

```powershell
Set-Location <StrangeUtaGame>
uv run --no-sync python D:\path\to\skill\scripts/check_sug_compatibility.py --repo . --project <project.sug>
python D:\path\to\skill\scripts/check_karaoke_environment.py --target .
```

The environment check may return non-zero when `ffmpeg` or `ffprobe` is absent;
that result is a diagnostic, not permission for production to download those
tools.
