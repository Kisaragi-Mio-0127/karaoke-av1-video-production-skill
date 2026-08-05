[简体中文](README.zh-CN.md) | English

# Karaoke AV1 Video Production Skill

A Codex skill plus a sanitized StrangeUtaGame integration for producing,
reviewing, rendering, validating, and packaging karaoke videos with editable
timing provenance and AV1 4:2:0 release checks.

## Included

- The complete workflow in `SKILL.md` and focused timing/release references.
- Nineteen sanitized StrangeUtaGame production scripts covering timing,
  Japanese ruby, ASR/MMS evidence, artwork, HEVC/AV1 rendering, inspection,
  finalization, archives, and snapshots.
- A guarded installer that copies the integration into a compatible
  StrangeUtaGame checkout and backs up overwritten files.
- A project/audio editor probe for manual timing review.
- A reproducible Windows dependency lock, environment checker, generic manifest,
  and private-override examples.

No recordings, lyrics, album metadata, fonts, cover art, model files, API
credentials, rendered media, or real project reports are included.

## Install the Codex skill

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

This repository is public. Cloning and reading it do not require GitHub
authentication; authentication is required only when pushing changes to a
repository for which you have write access. Invoke the skill in Codex with:

```text
$karaoke-av1-video-production
```

## Install the production scripts

The integration depends on the StrangeUtaGame application package; it is not a
replacement for that application. From this repository:

```powershell
python scripts/install_strangeutagame_integration.py --target D:\path\to\StrangeUtaGame --dry-run
python scripts/install_strangeutagame_integration.py --target D:\path\to\StrangeUtaGame
```

Then create the target repository's local environment:

```powershell
Set-Location D:\path\to\StrangeUtaGame
winget install --id=astral-sh.uv -e
uv python install 3.12
uv venv --python 3.12
uv pip install -r requirements-karaoke.skill.lock.txt
```

Install `ffmpeg`/`ffprobe` separately and provide a licensed CJK font. Rubber
Band is optional unless pitch shifting is requested; Whisper/MMS and external
MSST are optional evidence lanes. Full setup, official links, script routing,
and manifest usage are in
[`references/strangeutagame-integration.md`](references/strangeutagame-integration.md).

Check the environment:

```powershell
python scripts/check_karaoke_environment.py --target D:\path\to\StrangeUtaGame
```

## Script provenance and dependency boundary

All 19 production scripts in this repository are later-developed integration
scripts. They were untracked additions in the production working tree before
sanitization and packaging; they are not files taken from StrangeUtaGame's
upstream Git history. "Direct" below means importing tracked modules from the
separately obtained StrangeUtaGame application. "Transitive" means importing or
executing another bundled script that performs that import.

| Script | Boundary | Exact dependency or role |
|---|---|---|
| `karaoke_timing.py` | Direct upstream import | Imports domain entities, exporters, and `SugProjectParser`. |
| `karaoke_review_preview.py` | Direct upstream import | Imports `Character`, `Sentence`, and `SugProjectParser`. |
| `convert_english_sug_word_tokens.py` | Direct upstream import | Imports `SugProjectParser` and SUG timing-domain conversion helpers. |
| `sync_karaoke_editable_ruby.py` | Transitive runtime dependency | Imports contextual ruby and album timing data from the two StrangeUtaGame-backed scripts above. |
| `audit_karaoke_asr_recognition.py` | Transitive runtime dependency | Imports LRC helpers from `karaoke_timing.py`; importing that module loads StrangeUtaGame. |
| `audit_karaoke_mms_alignment.py` | Transitive runtime dependency | Imports `karaoke_timing.py` and reads SUG JSON timing evidence. |
| `render_karaoke_direct_av1_album.py` | Transitive runtime dependency | Executes `karaoke_review_preview.py` against SUG input to regenerate ASS. |
| `render_karaoke_direct_hevc444_album.py` | Transitive runtime dependency | Delegates to the direct AV1 renderer and therefore its SUG preview path. |
| `render_karaoke_direct_av1_420_album.py` | Transitive runtime dependency | Imports the ruby synchronizer and executes the SUG preview renderer. |
| `finalize_karaoke_release.py` | SUG artifact/layout dependency | Does not import the application, but verifies expected `.sug` files and integration release layout. |
| `karaoke_album.py` | No upstream-code import | Defines the sanitized manifest and path model used by the workflow. |
| `karaoke_language.py` | No upstream-code import | Provides language normalization and tokenization helpers. |
| `build_karaoke_wide_artwork.py` | No upstream-code import | Builds artwork with Pillow. |
| `render_vinyl_karaoke.py` | No upstream-code import | Builds the vinyl visual layer with media and image libraries. |
| `inspect_karaoke_media.py` | No upstream-code import | Inspects encoded media and shared render metadata. |
| `transcode_karaoke_av1.py` | No upstream-code import | Transcodes and verifies media with FFmpeg metadata. |
| `prepare_karaoke_msst_vocals.py` | No upstream-code import | Prepares optional evidence for an externally supplied MSST runner. |
| `package_karaoke_numbered_archives.py` | No upstream-code import | Packages numbered release archives from manifest paths. |
| `karaoke_release_snapshot.py` | No upstream-code import | Creates and restores release-file snapshots. |

Support tools have a separate boundary: `open_editable_project_with_audio_probe.py`
dynamically imports the application's GUI, persistence, and audio-loading modules;
`install_strangeutagame_integration.py` and `check_karaoke_environment.py` do not
import application code but explicitly operate on an existing checkout. The
authoritative machine-readable list is
[`integration/strangeutagame/dependency-manifest.json`](integration/strangeutagame/dependency-manifest.json).

## Repository layout

```text
.
├── SKILL.md
├── LICENSE
├── NOTICE.md
├── agents/
├── examples/
├── integration/strangeutagame/
│   ├── dependency-manifest.json # provenance and dependency boundary
│   ├── requirements/
│   └── scripts/                 # 19 sanitized production scripts
├── references/
├── scripts/
│   ├── check_karaoke_environment.py
│   ├── install_strangeutagame_integration.py
│   └── open_editable_project_with_audio_probe.py
└── tests/
```

## Tests

```powershell
python -m unittest discover -s scripts -p "test_*.py" -v
python -m unittest discover -s tests -p "test_*.py" -v
```

The repository tests parse every bundled script, load the generic manifest,
exercise installer conflict/backup/rollback guards, verify private-override
example shapes, require explicit network opt-in, and scan executable scripts
for machine-specific paths, credential forms, non-generic manifest defaults,
and fixed network cover defaults. They are packaging/safety tests, not a claim
that a full media render completed without private fixtures. Before production,
run the environment checker, all 19 command help smoke tests in the target
environment, a short authorized preview, and the release gates in `SKILL.md`.

## License and rights

The code is distributed under GPL-3.0-only; see `LICENSE`, `NOTICE.md`, and
`THIRD_PARTY_NOTICES.md`. Users must
hold the necessary rights for recordings, lyrics display/synchronization, cover
art, fonts, models, and final distribution. FFmpeg build licensing depends on
its compile configuration; see the [FFmpeg legal page](https://ffmpeg.org/legal.html).
