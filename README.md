# Karaoke AV1 Video Production Skill

[中文说明](README.zh-CN.md)

This repository packages a reusable Codex Skill and a guarded StrangeUtaGame
integration for Japanese karaoke timing and AV1 video production. The public
bundle is Japanese and language-neutral only; Chinese/English workflows stay
in the separate local Skill. Track-specific data remains in external manifests
and frozen lyric sources.

## Version baseline

Main targets StrangeUtaGame 1.5.0 with SUG 0.3.0; `sug-1.4.5` retains 1.4.5.
Gate compatibility on runtime `__version__` and the `SugMigrator` schema. The
official 1.5.0 tag still has `pyproject.toml` 1.2.6; that difference is
diagnostic only. The main installer rejects targets other than 1.5.0.

## What is automatic

The recommended Japanese entry is the single-command
`scripts/run_karaoke_japanese_full_auto.py`. Given a manifest, song ID, frozen
lyrics, and a new output directory, it:

- prepares the selected MSST vocal stem;
- builds a private initial SUG;
- runs Japanese MMS and creates an editable companion SUG;
- prepares the current layout and renders the AV1 MP4 delivery;
- exports a relocatable editable SUG with a verified media path.

The default quality policy is `auto-fallback`. Usable high-confidence MMS
timing is applied, while low-confidence or unresolved units retain canonical
timing and remain visible in the report. Manual or Agent timing adjustment is
optional after the companion SUG exists; it is not a prerequisite for the
automated run.

The frozen lyric source remains the default. Add
`--refresh-source` only to explicitly refresh one selected song from NetEase
into the `--source` destination. The command reads a supported embedded song
ID unless `--netease-song-id <numeric-id>` is supplied. Album display
metadata defaults to audio tags and then the song title/artist; use
`--metadata-source-audio` for transformed or tagless delivery audio.
When no frozen JSON exists, use `--lyrics-file <lyrics.lrc|lyrics.txt>` in its
place. LRC timestamps are preserved; plain UTF-8 text receives uniform coarse
anchors before acoustic alignment and remains timing-review input.

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

`local-mms-fa` remains the default alignment backend. The experimental
Japanese-only `NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn` backend is
available only with `--mms-backend nextfire-ja-latn`; it is not claimed to be
better than the default. It reads the fixed local
`models/hf/nextfire-mms-ja-latn` snapshot, never downloads at runtime, uses no
general Hugging Face cache, and executes no remote code.

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
   Install FFmpeg 8.x and FFprobe 8.x as the default matched pair under
   `<StrangeUtaGame>/tools/ffmpeg/bin`; see the
   [integration guide](references/strangeutagame-integration.md#ffmpeg-and-ffprobe).
   `--dry-run` deep-verifies and plans
   without writing or actively initiating network requests; `--offline` blocks
   model and Python downloads and passes uv offline mode.

Check an existing target without downloading or changing it:

```powershell
python scripts/check_karaoke_environment.py --target <StrangeUtaGame>
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --deep-verify
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --nextfire-mms-ja-latn
```

Review the plan, then explicitly bootstrap when setup is wanted:

```powershell
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --dry-run
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> `
  --accept-mms-cc-by-nc-4-0
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> `
  --nextfire-mms-ja-latn --dry-run
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> `
  --nextfire-mms-ja-latn --accept-nextfire-agpl-3-0 `
  --accept-mms-cc-by-nc-4-0
```

The optional NextFire install requires both acceptance flags. Its weights stay
local and are never committed to this repository; see the MMS workflow and
third-party notices for the license summary.

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

Install the bundled integration into a StrangeUtaGame 1.5.0 checkout. The main
installer rejects every other target. Review the dry run before allowing
replacements:

```powershell
python scripts/install_strangeutagame_integration.py --target <project> --dry-run
python scripts/install_strangeutagame_integration.py --target <project> --force
```

The installer copies only paths authorized by
[`dependency-manifest.json`](integration/strangeutagame/dependency-manifest.json)
and keeps rollback backups for replaced files.

## Dependency on upstream StrangeUtaGame

This repository does not contain or replace the upstream StrangeUtaGame
application. The integration bundle is installed into a compatible checkout
because several production scripts use its SUG domain model, parser, exporters,
and editor/audio interfaces:

- `karaoke_timing.py`, `render_karaoke_track.py`, `sug_ruby.py`, and
  `karaoke_mms_editable.py` import upstream Python modules directly.
- Full-auto, staged MMS, direct rerender, and batch entry scripts depend on
  those modules transitively and must run from the target checkout through its
  existing `.venv`.
- Media inspection, artwork, palette, pitch-shift, packaging, snapshot, and
  transcoding helpers do not import upstream code, but some still consume the
  manifest, SUG, font, media, or directory conventions of the target project.
- Repository-side installer and environment tools run outside the target, but
  receive the StrangeUtaGame checkout through `--target` and never substitute
  for the application itself.

The complete per-script dependency and installation map is in
[StrangeUtaGame integration](references/strangeutagame-integration.md). The
machine-readable source is
[`dependency-manifest.json`](integration/strangeutagame/dependency-manifest.json).

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

Replace `--source <frozen-lyrics.json>` with
`--lyrics-file <lyrics.lrc|lyrics.txt>` for a manually supplied lyric file.

To explicitly exercise the experimental Japanese-only backend, add
`--mms-backend nextfire-ja-latn`. The same dual-audio audit and
`auto-fallback`/`strict` quality policies still apply.

Japanese staged MMS/recovery:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> `
  --mms-model-path models/mms/model.pt `
  --quality-policy auto-fallback --output-dir <new-private-output-dir> `
  --visual-style spectrum
```

Add `--mms-backend nextfire-ja-latn` here as well when that explicit
experimental option is intended; do not combine it with `--mms-model-path`.

The staged wrapper accepts optional `--source`, `--sug`, and `--vocals-root`
overrides. It does not download a missing MMS checkpoint during production.

Full-auto, staged MMS, and existing-SUG rerender commands accept
`--output-mode subtitle-overlay`. Without `--background-video`, this writes a
silent transparent ProRes 4444 MOV for editor compositing. With
`--background-video <footage>`, FFmpeg produces the normal AV1/AAC MP4 directly:
long footage is trimmed to the song interval, while short footage is followed
by black through the remaining interval. Encoding probes `av1_nvenc` first and
automatically retries `libaom-av1` when NVENC is unavailable or its render fails.

Japanese video rerender from an existing adjusted SUG:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <adjusted-project.sug> --audio <post-mix-audio> `
  --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --visual-style spectrum
```

Album title and artist come from audio tags by default. Pass
`--album-title` and `--album-artist` only as explicit overrides.

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

Artwork is automatic when a standard deliverable cover or embedded audio cover
is available. Use `--cover` for an explicit image; composition, background, and
cover-source-audio overrides remain optional.

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
- `ruff.toml`: Ruff lint configuration for this repository; it does not create
  a Python environment or affect production rendering.

## Documentation

| Topic | English | 简体中文 |
| --- | --- | --- |
| Full-auto and MMS | [English](references/mms-workflows.md) | [中文](references/mms-workflows.zh-CN.md) |
| StrangeUtaGame integration and per-script dependencies | [English](references/strangeutagame-integration.md) | [中文](references/strangeutagame-integration.zh-CN.md) |
| ASR, SUG, and pitch shifting | [English](references/asr-sug-pitch.md) | [中文](references/asr-sug-pitch.zh-CN.md) |
| Wide visual templates | [English](references/wide-visual-templates.md) | [中文](references/wide-visual-templates.zh-CN.md) |
| Subtitle and timing quality | [English](references/subtitle-timing-quality.md) | [中文](references/subtitle-timing-quality.zh-CN.md) |
| AV1 4:2:0 commands | [English](references/av1-420-commands.md) | [中文](references/av1-420-commands.zh-CN.md) |
| Batch release gates | [English](references/batch-release-gates.md) | [中文](references/batch-release-gates.zh-CN.md) |
| Singer colours and overlays | [English](references/singer-overlays.md) | [中文](references/singer-overlays.zh-CN.md) |
| Third-party component notices | [English](THIRD_PARTY_NOTICES.md) | [中文](THIRD_PARTY_NOTICES.zh-CN.md) |

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
