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

## Per-script upstream StrangeUtaGame dependencies

These integration files were developed after and outside StrangeUtaGame; they
are not files from its upstream Git history, and this repository does not own
or contain the upstream `strange_uta_game` source. In the table below,
"independent" means independent of upstream StrangeUtaGame code and checkout
resources. It does not mean independent of the integration's own Python
packages, input media, fonts, FFmpeg, models, or other explicitly listed tools.

The installer places every manifest-authorized Python path under
`<target>/scripts/` (including the `karaoke_common` and `karaoke_japanese`
package directories). It places the two requirements files at
the target root. The upstream package remains at
`<target>/src/strange_uta_game/` and is made importable in the target's one
`.venv` by the pinned requirements file's local `-e .` entry. MMS and Whisper
assets are separate project-owned runtime files under `<target>/models/`.

### Direct or transitive upstream code dependencies

| Script/module | Upstream module, resource, or runtime dependency | Installed location | Independent of upstream? |
|---|---|---|---|
| `karaoke_timing.py` | Directly imports `backend.application.auto_check_service`, `backend.domain`, `backend.infrastructure.exporters`, and `backend.infrastructure.persistence.sug_io`; also uses SUG projects, fonts, Whisper/stable-ts, and FFmpeg. | `<target>/scripts/karaoke_timing.py` | No. |
| `render_karaoke_track.py` | Builds ASS and renders one track. It directly imports upstream `Character`, `Sentence`, and `SugProjectParser`, while `karaoke_common/visuals.py` owns the vinyl and spectrum FFmpeg graphs. | `<target>/scripts/render_karaoke_track.py` | No. |
| `karaoke_mms_editable.py` | Directly imports `SugProjectParser` from upstream SUG persistence and reads/writes SUG companions. | `<target>/scripts/karaoke_mms_editable.py` | No. |
| `sug_ruby.py` | Its object-writeback path dynamically imports upstream `Ruby` and `RubyPart`; raw-JSON inspection and validation paths do not require that import. | `<target>/scripts/sug_ruby.py` | Partial: JSON-only validation can run without upstream; object writeback cannot. |
| `audit_karaoke_asr_recognition.py` | Imports LRC/correction helpers from `karaoke_timing.py`, so loading those helpers initializes the upstream imports; additionally requires project-owned Whisper weights and stable-whisper/torch runtime. | `<target>/scripts/audit_karaoke_asr_recognition.py` | No for the supported audit path. |
| `audit_karaoke_mms_alignment.py` | Imports `karaoke_timing.py` and canonical ruby helpers; consumes SUG timing plus original/MSST audio and loads the local `models/mms/model.pt` through torchaudio MMS_FA. | `<target>/scripts/audit_karaoke_mms_alignment.py` | No. |
| `build_karaoke_mms_overrides.py` | Imports timing structures/helpers from `karaoke_timing.py` and consumes SUG/MMS audit artifacts. | `<target>/scripts/build_karaoke_mms_overrides.py` | No. |
| `sync_karaoke_editable_ruby.py` | Uses `sug_ruby.py` against SUG project data; the canonical object's writeback path depends on upstream domain classes. | `<target>/scripts/sync_karaoke_editable_ruby.py` | No for the supported integrated writeback workflow. |
| `karaoke_workflow.py` | Imports and launches `render_karaoke_track.py` with the same Python executable; therefore inherits its SUG/timing upstream imports. It also uses the target project root, assets, FFmpeg, and release helpers. | `<target>/scripts/karaoke_workflow.py` | No. |
| `render_karaoke_direct_av1_420_album.py` | Executes `render_karaoke_track.py` per render task and therefore inherits its direct upstream parser/domain dependency; also uses SUG files, artwork/font assets, and FFmpeg AV1 encoders. | `<target>/scripts/render_karaoke_direct_av1_420_album.py` | No. |
| `run_karaoke_japanese_workflow.py` | Thin entry over `karaoke_workflow.py`; inherits its preview, SUG, project-layout, and FFmpeg dependencies. | `<target>/scripts/run_karaoke_japanese_workflow.py` | No. |
| `run_karaoke_japanese_mms_workflow.py` | Imports MMS audit/build, `karaoke_mms_editable.py`, `render_karaoke_track.py`, and `karaoke_workflow.py`; requires canonical/companion SUG files, local MMS model, audio stems, fonts, and FFmpeg. | `<target>/scripts/run_karaoke_japanese_mms_workflow.py` | No. |
| `karaoke_full_auto.py` | Imports `karaoke_timing.py`, ASR, and MSST preparation, then lazily imports the Japanese MMS workflow; requires the target manifest/layout, upstream SUG runtime, local MMS/Whisper models, MSST adapter, and FFmpeg. | `<target>/scripts/karaoke_full_auto.py` | No. |
| `run_karaoke_japanese_full_auto.py` | Japanese-only entry over `karaoke_full_auto.py`; inherits the complete timing, MMS, SUG, MSST, model, and render dependency chain. | `<target>/scripts/run_karaoke_japanese_full_auto.py` | No. |

### Artifact/layout dependencies without upstream code imports

| Script/module | Upstream module, resource, or runtime dependency | Installed location | Independent of upstream? |
|---|---|---|---|
| `finalize_karaoke_release.py` | Imports no upstream code, but validates expected canonical/companion `.sug` artifacts and the integration release layout; uses the shared FFmpeg resolver. | `<target>/scripts/finalize_karaoke_release.py` | Conditional: independent of upstream code, not of existing SUG artifacts/layout. |
| `build_karaoke_wide_artwork.py`<br>`karaoke_cover_palette.py`<br>`karaoke_color_plan.py`<br>`karaoke_common/artwork.py` | No upstream import. These build deterministic artwork/palettes with Pillow and integration-owned inputs. | Corresponding paths under `<target>/scripts/` | Yes, with their declared images/fonts/metadata. |
| `inspect_karaoke_media.py`<br>`transcode_karaoke_av1.py`<br>`render_vinyl_karaoke.py`<br>`pitch_shift_audio.py` | No upstream import. They use media/manifest metadata and external runtimes: FFmpeg (and FFprobe where selected); pitch shifting additionally requires Rubber Band 3.x. | Corresponding paths under `<target>/scripts/` | Yes, with the required media and external commands. |
| `prepare_karaoke_msst_vocals.py` | No upstream import. It loads an external local `prepare_sovits41_msst_stems.py` adapter and its MSST runtime/model files, owned outside this integration. | `<target>/scripts/prepare_karaoke_msst_vocals.py` | Yes with respect to StrangeUtaGame; no with respect to the separate MSST adapter/runtime. |
| `karaoke_album.py`<br>`karaoke_language.py`<br>`karaoke_release_snapshot.py`<br>`karaoke_direct_album_planning.py`<br>`package_karaoke_numbered_archives.py` | No upstream import. They operate on integration manifests, paths, snapshots, or release files; album planning uses the shared FFmpeg resolver for media validation. | Corresponding paths under `<target>/scripts/` | Yes, with their declared integration inputs. |
| `karaoke_model_paths.py` | No upstream import; resolves only project-owned `models/mms/model.pt` and `models/whisper/` paths. | `<target>/scripts/karaoke_model_paths.py` | Yes with respect to upstream code; model files are still required by callers. |
| `karaoke_common/layout.py`<br>`karaoke_japanese/layout.py` | No upstream import; these are the public general and Japanese layout definitions. Chinese/English layouts are not part of this repository. | Corresponding package paths under `<target>/scripts/` | Yes; modules, not standalone commands. |
| `karaoke_common/visuals.py` | No upstream import; owns the vinyl and spectrum FFmpeg filter graphs used by the track renderer. | `<target>/scripts/karaoke_common/visuals.py` | Yes; module, not a standalone command. |
| `karaoke_common/device.py` | No upstream import; dynamically loads `torch` to select CPU/CUDA. | `<target>/scripts/karaoke_common/device.py` | Yes; module, not a standalone command. |
| `karaoke_common/pronunciation.py` | No direct upstream import; uses the JSON-capable portions of `sug_ruby.py` to enforce pronunciation policy. | `<target>/scripts/karaoke_common/pronunciation.py` | Yes for its validation path; module, not a standalone command. |
| `karaoke_common/__init__.py`<br>`karaoke_japanese/__init__.py` | Package initializers only; dependencies are those of the package members they export. | Corresponding package paths under `<target>/scripts/` | Yes for upstream code; not standalone commands. |

### Skill-side installation and compatibility tools

These tools stay in the Skill checkout; the integration installer does not
copy them into the target.

| Tool | Upstream module, resource, or runtime dependency | Location | Independent of upstream? |
|---|---|---|---|
| `install_strangeutagame_integration.py` | Requires a compatible target layout containing `pyproject.toml`, `src/strange_uta_game/`, and `scripts/`; copies only manifest-authorized integration files. | `<skill>/scripts/` | No: it needs a target checkout, but does not import upstream code. |
| `check_sug_compatibility.py` | Directly imports upstream version, `SugMigrator`, and `SugProjectParser` from `<target>/src`; reads representative SUG projects without saving them. | `<skill>/scripts/` | No. |
| `open_editable_project_with_audio_probe.py` | Dynamically imports upstream GUI/app directories, timing loader/interface, project store, SUG persistence, and the target `main` module; also probes upstream audio-converter hooks and media. | `<skill>/scripts/` | No. |
| `check_karaoke_environment.py`<br>`bootstrap_karaoke_environment.py`<br>`karaoke_bootstrap.py` | Require a compatible target layout and target `.venv`; probe or install the manifest's Python modules, including the target's editable `strange_uta_game`, and manage project-owned model files. They also probe `git`, `uv`, FFmpeg/FFprobe, and hardware runtime. | `<skill>/scripts/` | No: their purpose is to check/bootstrap a target checkout. |
| Skill-side `pitch_shift_audio.py` | No upstream dependency; standalone FFmpeg/FFprobe/Rubber Band 3.x utility. | `<skill>/scripts/pitch_shift_audio.py` | Yes. |

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
