# StrangeUtaGame Integration

[简体中文](strangeutagame-integration.zh-CN.md) | English

Use this integration only with an authorized StrangeUtaGame checkout. The
bundled files are a sanitized pipeline snapshot, not the full GUI application.
They are later-developed integration scripts rather than files from the
upstream Git history. See the README and
`integration/strangeutagame/dependency-manifest.json` for the direct,
transitive, artifact-layout, and no-upstream-import classifications.

## Install the scripts

Preview the copy operation first:

```powershell
$skillRoot = (Resolve-Path .).Path
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target D:\path\to\StrangeUtaGame --dry-run
```

Install after reviewing the JSON plan:

```powershell
$skillRoot = (Resolve-Path .).Path
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target D:\path\to\StrangeUtaGame
```

The installer validates `pyproject.toml`, `src/strange_uta_game`, and `scripts`.
It refuses to replace differing files unless `--force` is supplied. Forced
replacement first copies old files to `.karaoke-skill-backup/<UTC stamp>/`.
The Python copy set is not a directory glob: it is the union of manifest
`scripts`, `shared_modules`, and recursive `package_files`. Package directories
are installed with their parent structure, so a new import cannot be documented
without also being installed.

## Python environment

Python 3.12 is the tested baseline; the public scripts require Python 3.10 or
newer. Install `uv` from the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).
On Windows, the official WinGet command is:

```powershell
winget install --id=astral-sh.uv -e
```

From the StrangeUtaGame root:

```powershell
uv python install 3.12
uv venv --python 3.12
uv pip install -r requirements-karaoke.skill.lock.txt
```

The lock installs StrangeUtaGame editable plus GUI, rendering, timing,
Whisper/stable-ts, PyTorch, and MMS dependencies. Use the copied `.in` file as
the human-readable dependency source. Keep this environment project-local.

The required and tested versions are StrangeUtaGame 1.4.5 and SUG storage
format 0.3.0. Keep the application version synchronized in
`src/strange_uta_game/__version__.py` and `pyproject.toml`, and read the storage
format from `SugMigrator.CURRENT_VERSION`. Run the compatibility checker from
the Skill repository against representative projects after an application
update, for example:

```powershell
Set-Location D:\path\to\karaoke-av1-video-production-skill
python scripts/check_sug_compatibility.py --repo D:\path\to\StrangeUtaGame --project D:\path\to\representative.sug
```

Use the configured language profile for the documented ASR/alignment path. The
bundled public route is Japanese through
`run_karaoke_japanese_workflow.py`; any non-default profile requires a
separately validated adapter.
Require unchanged before/after project hashes.

## Native tools

- Install `ffmpeg` and `ffprobe` from a build linked by the [official FFmpeg download page](https://ffmpeg.org/download.html). Confirm that the build exposes the `subtitles`/libass filter and either `av1_nvenc` or `libaom-av1`.
- Install current NVIDIA drivers only when using `av1_nvenc`; CPU AV1 remains the fallback.
- Install Rubber Band separately only for pitch shifting. The R3/Finer engine and formant-preservation guidance are documented by the [official Rubber Band project](https://breakfastquay.com/rubberband/). It is not required for an unshifted render.
- Supply a legally usable CJK font yourself. The repository does not download or redistribute a font package.
- Supply Whisper/MMS/MSST model files separately and review their licenses. MSST also requires `--external-script` or `KARAOKE_MSST_HELPER`.

Run the environment report from this skill repository:

```powershell
Set-Location D:\path\to\karaoke-av1-video-production-skill
python scripts/check_karaoke_environment.py --target D:\path\to\StrangeUtaGame
```

`rubberband`, stable-ts, Torch, and torchaudio are reported separately from the
core Git/uv/FFmpeg/StrangeUtaGame checks.

## Private project data

Copy `examples/album.example.json` into a private project area, replace every
placeholder, and pass it explicitly:

```powershell
$env:KARAOKE_ALBUM_MANIFEST = "D:\private\album.json"
uv run python scripts/karaoke_timing.py --manifest $env:KARAOKE_ALBUM_MANIFEST --allow-partial-manifest
```

Five-track release mode remains the default compatibility profile. Use
`--allow-partial-manifest` for a smaller collection. Never commit real album
manifests, hashes, lyrics, timing overrides, fonts, reports, or media to this
skill repository.

Optional song-specific display and ruby decisions belong in private JSON files
based on the examples. Use these only for approved exceptions or escalated
ambiguity, proper nouns, artistic readings, evidence conflicts, low confidence,
or `unresolved` results; preserve existing human-reviewed or legacy ruby.
When supported by the installed snapshot, pass them through
`KARAOKE_DISPLAY_OVERRIDES` and `KARAOKE_RUBY_GROUP_OVERRIDES`, then ensure the
accepted result is written to the canonical SUG before rendering. Contextual
timing readings use `KARAOKE_TIMING_READING_OVERRIDES`; start from the matching
empty example.

Network access is disabled by default. `karaoke_timing.py` requires an existing
authorized lyric source unless `--refresh-source` is supplied explicitly.
`render_vinyl_karaoke.py` uses embedded artwork unless `--allow-network` is
supplied; network covers must use HTTPS, resolve only to public addresses, not
redirect, and stay within 25 MiB.

Runtime reports are private evidence and may intentionally include local paths,
media hashes, model paths, or source URLs needed for provenance. Keep them under
ignored `deliverables/`, `validation/`, or `sources/` directories and redact
them before sharing. Do not commit probe, audit, validation, or snapshot JSON.

The Japanese workflow keeps vinyl rotating. Every formal or test invocation
rebuilds `artwork-current/vinyl.png` with
`direction-neutral-concentric-grooves/v3/backplate-absent`, records the
generator and `vinyl_sha256`, and passes that generated path explicitly to the
preview/render command. The canonical vinyl path is an identity input only.
When artwork comes from a different file than the delivery audio, record
`cover-source-audio` separately.

The wide composition follows `wide-layout-v5/no-right-panels`: the rotating vinyl
card is `(40,30,340,402)`, footer bottom padding is `12`, and the lower
subtitle panel starts at `y=576`. The extra outer right-panel overlay and the
compact dark backplate behind/below the record are absent; the album card, card
footer, and bottom subtitle panel remain visible. Reports use
`right_panel_visible=false`, `outer_right_panel_visible=false`,
`vinyl_backplate_present=false`, and `vinyl_backplate_preserved=false`.
The spectrum variant uses clip-safe geometry `(736,226,1168,348)`, 64 px
horizontal glow padding, 56 px top/bottom glow padding, and 8 px top/bottom
bar clearance; inspect original-resolution frames for unclipped peaks and glow.

## Script map

| Stage | Installed entry points |
|---|---|
| Manifest/text | `karaoke_album.py`, `karaoke_language.py` |
| Timing and editable SUG | `karaoke_timing.py`, `karaoke_review_preview.py`, `sync_karaoke_editable_ruby.py` |
| Alignment evidence | `audit_karaoke_asr_recognition.py`, `audit_karaoke_mms_alignment.py` |
| Optional separation evidence | `prepare_karaoke_msst_vocals.py` |
| Artwork/render | `build_karaoke_wide_artwork.py`, `render_vinyl_karaoke.py`, `render_karaoke_direct_av1_album.py`, `render_karaoke_direct_hevc444_album.py`, `render_karaoke_direct_av1_420_album.py` |
| Japanese/general workflows | `karaoke_workflow.py`, `run_karaoke_japanese_workflow.py`, `build_karaoke_mms_overrides.py` |
| Media/release | `inspect_karaoke_media.py`, `transcode_karaoke_av1.py`, `finalize_karaoke_release.py`, `package_karaoke_numbered_archives.py`, `karaoke_release_snapshot.py` |

The renderer packages installed with those entries are governed by
`dependency-manifest.json` under `package_files`; the manifest is the authority
for the exact installed tree.

Support tools in this repository include `scripts/check_sug_compatibility.py`
for read-only SUG validation and `scripts/pitch_shift_audio.py` for complete-
mix pitch shifting. The compatibility checker stays in the Skill repository;
it is not copied into the target checkout. The latter is also installed with
the production script snapshot; keep its verified FLAC and JSON report together.

The MMS override freezer is bundled; keep song-specific inputs and outputs
private. When a key change is requested, run
`scripts/pitch_shift_audio.py` on the complete mix before timing and rendering;
use the verified shifted FLAC for timing evidence and the default MP4 AAC-LC
320 kb/s output. MKV is strictly opt-in through `--lossless-companion` (or an
underlying explicit `--lossless-output`) and is rejected for MP3/AAC or any
non-FLAC/PCM source. Do not use deterministic interpolation as an independent-
ASR fallback; an unavailable or failed independent ASR lane is `unresolved`.
Use the configured language profile and keep source text, applicable ruby, and
readings unchanged except for reviewed profile-specific normalization. MKV is
strictly opt-in: create it only when the user explicitly requests
`--lossless-companion` (or an underlying explicit `--lossless-output`) and the
source is probed as FLAC or PCM WAV; otherwise do not create or report it.

## Production order

```text
manifest → Japanese workflow → optional MSST evidence → ASR/MMS audit → source lyrics
→ candidate ruby fills in canonical SUG → Agent full-context ruby audit/writeback
→ timing/phrase decisions → read-only renderer → ASS/report/final frames → artwork
→ HEVC/AV1 render → media inspection → finalization → archives/snapshot
```

Pronunciation validation exposes `optional`, `required`, and `off`, with
`optional` as the default. For Japanese, structural ruby boundaries and
SUG/ASS/final-frame agreement remain mandatory; in `optional` mode a missing
pronunciation sidecar is recorded as not performed and does not block by
default. Validate a supplied sidecar when present, and use `required` only for
an explicitly requested pronunciation gate. Protect human-reviewed or legacy
ruby. The renderer is read-only over the reviewed canonical SUG and must not
infer or overwrite ruby. Record per-span status, confidence, evidence,
model/prompt version, and before/after SUG hashes where spans exist. Manual
timing review remains optional by default. When required, use the editor audio
probe first, then open the verified canonical SUG in normal editable mode. The
final editable timing source remains `.sug`; JSON probe reports are evidence,
not project files.
