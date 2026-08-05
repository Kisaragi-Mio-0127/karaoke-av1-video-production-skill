# StrangeUtaGame Integration

Use this integration only with an authorized StrangeUtaGame checkout. The
bundled files are a sanitized pipeline snapshot, not the full GUI application.

## Install the scripts

Preview the copy operation first:

```powershell
python scripts/install_strangeutagame_integration.py --target D:\path\to\StrangeUtaGame --dry-run
```

Install after reviewing the JSON plan:

```powershell
python scripts/install_strangeutagame_integration.py --target D:\path\to\StrangeUtaGame
```

The installer validates `pyproject.toml`, `src/strange_uta_game`, and `scripts`.
It refuses to replace differing files unless `--force` is supplied. Forced
replacement first copies old files to `.karaoke-skill-backup/<UTC stamp>/`.

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

## Native tools

- Install `ffmpeg` and `ffprobe` from a build linked by the [official FFmpeg download page](https://ffmpeg.org/download.html). Confirm that the build exposes the `subtitles`/libass filter and either `av1_nvenc` or `libaom-av1`.
- Install current NVIDIA drivers only when using `av1_nvenc`; CPU AV1 remains the fallback.
- Install Rubber Band separately only for pitch shifting. The R3/Finer engine and formant-preservation guidance are documented by the [official Rubber Band project](https://breakfastquay.com/rubberband/). It is not required for an unshifted render.
- Supply a legally usable CJK font yourself. The repository does not download or redistribute a font package.
- Supply Whisper/MMS/MSST model files separately and review their licenses. MSST also requires `--external-script` or `KARAOKE_MSST_HELPER`.

Run the environment report from this skill repository:

```powershell
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
based on the examples. Point the renderer at them with
`KARAOKE_DISPLAY_OVERRIDES` and `KARAOKE_RUBY_GROUP_OVERRIDES` when supported by
the installed snapshot. Contextual timing readings use
`KARAOKE_TIMING_READING_OVERRIDES`; start from the matching empty example.

Network access is disabled by default. `karaoke_timing.py` requires an existing
authorized lyric source unless `--refresh-source` is supplied explicitly.
`render_vinyl_karaoke.py` uses embedded artwork unless `--allow-network` is
supplied; network covers must use HTTPS, resolve only to public addresses, not
redirect, and stay within 25 MiB.

Runtime reports are private evidence and may intentionally include local paths,
media hashes, model paths, or source URLs needed for provenance. Keep them under
ignored `deliverables/`, `validation/`, or `sources/` directories and redact
them before sharing. Do not commit probe, audit, validation, or snapshot JSON.

## Script map

| Stage | Installed entry points |
|---|---|
| Manifest/language | `karaoke_album.py`, `karaoke_language.py` |
| Timing and editable SUG | `karaoke_timing.py`, `karaoke_review_preview.py`, `sync_karaoke_editable_ruby.py`, `convert_english_sug_word_tokens.py` |
| Alignment evidence | `audit_karaoke_asr_recognition.py`, `audit_karaoke_mms_alignment.py` |
| Optional separation evidence | `prepare_karaoke_msst_vocals.py` |
| Artwork/render | `build_karaoke_wide_artwork.py`, `render_vinyl_karaoke.py`, `render_karaoke_direct_av1_album.py`, `render_karaoke_direct_hevc444_album.py`, `render_karaoke_direct_av1_420_album.py` |
| Media/release | `inspect_karaoke_media.py`, `transcode_karaoke_av1.py`, `finalize_karaoke_release.py`, `package_karaoke_numbered_archives.py`, `karaoke_release_snapshot.py` |

The album-specific automatic timing-override generator and one-off spectrum
comparison utility are intentionally not bundled. They contained real song
text or machine-specific executable paths. Use private override JSON and the
separate pitch-shift skill instead.

## Production order

```text
manifest → optional MSST evidence → ASR/MMS audit → reviewed overrides
→ SUG/ASS/LRC/SRT timing → ruby synchronization/preview → artwork
→ HEVC/AV1 render → media inspection → finalization → archives/snapshot
```

Manual timing review is optional by default. When required, use the editor
audio probe first, then open the verified canonical SUG in normal editable mode.
The final editable timing source remains `.sug`; JSON probe reports are evidence,
not project files.
