[简体中文](README.zh-CN.md) | English

# Karaoke AV1 Video Production Skill

A Codex skill and a sanitized StrangeUtaGame integration for producing,
reviewing, rendering, validating, and packaging karaoke videos with editable
timing provenance and AV1 4:2:0 release checks.

The bundled default language profile is Japanese (`ja`); other languages can
be connected through separately validated adapters.

Start with [SKILL.md](SKILL.md); the [中文 README](README.zh-CN.md) and the
English/Chinese reference pairs below are maintained together.

## Included

- An inspect → preview → encode → verify workflow in `SKILL.md`.
- Semantic phrase segmentation, ruby word-boundary QA, editable SUG parity,
  MMS and independent-ASR evidence, and lyric visual-fit gates.
- Wide-layout `vinyl` and `spectrum` templates; choose exactly one per render.
- Legacy-compatible default video delivery at 1920x1080 30 fps yuv420p BT.709:
  AV1 NVENC CQ44, preset p7, tune hq, VBR, full-resolution multipass,
  lookahead32, spatial/temporal AQ, strength8, GOP240; default MP4 audio is
  AAC-LC 320 kb/s, with an optional lossless-audio version from a genuinely
  lossless source.
- Complete-mix pitch shifting through `scripts/pitch_shift_audio.py`, using
  Rubber Band R3 Finer with formant preservation by default; formal runs reject
  MP3/AAC sources instead of relabeling lossy audio as FLAC.
- StrangeUtaGame 1.4.5 / SUG storage format 0.3.0 as the current tested
  baseline. The `pyproject.toml` package version may still say 1.2.6; it is
  not the application or parser version authority.
- Nineteen distinct sanitized production entry-script implementations plus the
  shared `sug_ruby.py` canonical-facts module. The pitch tool
  is also mirrored at `scripts/pitch_shift_audio.py` for standalone use, plus a guarded installer, an editor/audio
  probe, environment checks, the read-only top-level
  `scripts/check_sug_compatibility.py` validator, manifests, and
  private-override examples.

The dependency manifest keeps the 19 entry scripts in `scripts` and records
`sug_ruby.py` separately under `shared_modules`; the shared module is not an
additional entry point.

No recordings, lyrics, album metadata, fonts, cover art, models, credentials,
rendered media, or real project reports are included.

## Install the skill and integration

Clone the public repository into the Codex skills directory:

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

Invoke it in Codex with:

```text
$karaoke-av1-video-production
```

The integration depends on an authorized StrangeUtaGame checkout. Preview the
copy plan, then install it:

```powershell
$projectRoot = (Resolve-Path .\private-project).Path
python scripts/install_strangeutagame_integration.py --target $projectRoot --dry-run
python scripts/install_strangeutagame_integration.py --target $projectRoot
```

Create the checkout's project-local environment:

```powershell
$projectRoot = (Resolve-Path .\private-project).Path
Set-Location $projectRoot
winget install astral-sh.uv
uv python install 3.12
uv venv --python 3.12
uv pip install -r requirements-karaoke.skill.lock.txt
```

Install `ffmpeg`/`ffprobe` separately and provide a licensed CJK font. Rubber
Band is needed only for pitch shifting; Whisper/MMS and external MSST are
optional evidence lanes. Run:

```powershell
$projectRoot = (Resolve-Path .\private-project).Path
python scripts/check_karaoke_environment.py --target $projectRoot
```

See the [integration guide](references/strangeutagame-integration.md) for
official links, script routing, private manifests, and network boundaries.

## Production rules

1. Build a rights manifest for recordings, lyrics, synchronization/display,
   fonts, artwork, models, and final distribution. Stop public delivery when
   a required right is missing or uncertain.
2. Probe every input and define the output matrix before encoding. Preserve
   source media and write to a temporary output until all mandatory gates pass.
3. Use the configured language profile for the documented ASR and alignment
   path; require a validated adapter for any non-default profile. Independent
   ASR is a separate evidence lane, never a silent
   fallback for failed forced alignment; an unavailable or failed lane is
   recorded as `unresolved`.
4. When pitch shifting is requested, shift the complete mix before timing and
   rendering. Feed the verified shifted FLAC into alignment evidence, previews,
   MP4 AAC-LC 320 kb/s, and the paired MKV FLAC track.
5. Use the legacy-compatible AV1 profile by default: NVENC CQ44, preset p7,
   tune hq, VBR, full-resolution multipass, lookahead32, spatial/temporal AQ,
   strength8, GOP240, 1920x1080 30 fps, yuv420p, and BT.709. Keep MP4 as the
   AAC-LC 320k compatibility version; make a separate lossless version only
   from a genuinely lossless source.
6. Validate MP4 and MKV as one generation: AAC-LC/320k metadata, FLAC-only MKV
   audio, identical encoded video-stream hashes, matching timeline bounds, and
   decoded MKV PCM equal to the selected lossless source slice.
7. Generate ruby candidates only for missing spans and write them to canonical
   SUG, preserving human-reviewed or legacy ruby. The Agent audits every span
   in full lyric, grammar, inflection, lexical-boundary, and contextual-reading
   context and can approve or write corrections back. If unchanged, retain the
   default ruby. Escalate only ambiguity, proper nouns, artistic readings,
   evidence conflicts, low confidence, or `unresolved`; the renderer reads only
   the reviewed SUG and cannot infer or overwrite ruby. Its review sidecar must
   match the current SUG hash and contain an approved exact-span record for every
   stored ruby span; missing, stale, machine-only, low-confidence, conflicting,
   or unresolved records fail closed. Publish the SUG atomically before its
   sidecar so an interrupted write cannot certify a SUG that was never stored.
   Require SUG, ASS/report, and final-frame agreement, with per-span status,
   confidence, evidence, model/prompt version, and before/after SUG hashes.
8. Keep source text, applicable ruby, and contextual readings traceable from
   the editable SUG through ASS and the rendered output.
9. Confirm the installed application and `SugMigrator.CURRENT_VERSION`; do not
   use the stale `pyproject.toml` package version as the SUG contract.

## Reference guides

Every English reference has a Chinese counterpart and reciprocal links:

| Topic | English | 中文 |
|---|---|---|
| AV1, FFmpeg, MP4/MKV | [av1-420-commands.md](references/av1-420-commands.md) | [av1-420-commands.zh-CN.md](references/av1-420-commands.zh-CN.md) |
| SUG, independent ASR, pitch | [asr-sug-pitch.md](references/asr-sug-pitch.md) | [asr-sug-pitch.zh-CN.md](references/asr-sug-pitch.zh-CN.md) |
| Wide vinyl/spectrum | [wide-visual-templates.md](references/wide-visual-templates.md) | [wide-visual-templates.zh-CN.md](references/wide-visual-templates.zh-CN.md) |
| Subtitle timing and quality | [subtitle-timing-quality.md](references/subtitle-timing-quality.md) | [subtitle-timing-quality.zh-CN.md](references/subtitle-timing-quality.zh-CN.md) |
| Batch release | [batch-release-gates.md](references/batch-release-gates.md) | [batch-release-gates.zh-CN.md](references/batch-release-gates.zh-CN.md) |
| StrangeUtaGame integration | [strangeutagame-integration.md](references/strangeutagame-integration.md) | [strangeutagame-integration.zh-CN.md](references/strangeutagame-integration.zh-CN.md) |

## Private project data

Copy `examples/album.example.json` into a private project area, replace every
placeholder, and pass it explicitly:

```powershell
$env:KARAOKE_ALBUM_MANIFEST = (Resolve-Path .\private\album.json).Path
uv run python scripts/karaoke_timing.py --manifest $env:KARAOKE_ALBUM_MANIFEST --allow-partial-manifest
```

Keep song-specific display, ruby, and contextual reading decisions in private
JSON through `KARAOKE_DISPLAY_OVERRIDES`, `KARAOKE_RUBY_GROUP_OVERRIDES`, and
`KARAOKE_TIMING_READING_OVERRIDES`. Use ruby overrides only for approved
exceptions or escalated cases, preserve existing human/legacy ruby, and merge
accepted decisions into canonical SUG before rendering. Network access is off
by default; source refresh and public cover retrieval require explicit opt-in.

## Script provenance and dependency boundary

The 19 production entry scripts are later-developed integration scripts; the
shared `sug_ruby.py` module is recorded separately under `shared_modules` and
is not an entry. They were
untracked additions in the production working tree before sanitization and are
not files from StrangeUtaGame's upstream Git history. “Direct upstream import”
means importing tracked modules from a separately obtained application;
“transitive runtime dependency” means loading those modules through another
bundled script.

| Script | Boundary | Role or dependency |
|---|---|---|
| `karaoke_timing.py` | Direct upstream import | Domain entities, exporters, and `SugProjectParser`. |
| `karaoke_review_preview.py` | Direct upstream import | `Sentence` and `SugProjectParser`. |
| `sync_karaoke_editable_ruby.py` | Transitive runtime dependency | SUG-first Agent review workflow: without `--patches` it performs a read-only structural audit and writes nothing; with an explicit review-patch JSON, it writes accepted ruby changes to canonical SUG and the sibling `.ruby-review.json` sidecar. Sidecars may contain lyric surfaces and generation IDs, are ignored by Git, and must remain private. |
| `sug_ruby.py` | Shared module; direct upstream import on writeback | Canonical SUG ruby validation, hashes, sidecar records, and a lazy candidate helper; object writeback dynamically imports `Character` and `Sentence`. Recorded under `shared_modules`, not an entry script. |
| `audit_karaoke_asr_recognition.py` | Transitive runtime dependency | LRC helpers and application-backed timing. |
| `audit_karaoke_mms_alignment.py` | Transitive runtime dependency | Timing helpers and SUG evidence. |
| `render_karaoke_direct_av1_album.py` | Transitive runtime dependency | Regenerates ASS through the SUG preview path. |
| `render_karaoke_direct_hevc444_album.py` | Transitive runtime dependency | Delegates to the direct AV1 renderer. |
| `render_karaoke_direct_av1_420_album.py` | Transitive runtime dependency | Reads reviewed canonical SUG for ruby synchronization and SUG preview rendering; it must not infer or overwrite ruby. |
| `finalize_karaoke_release.py` | SUG artifact/layout dependency | Checks `.sug` files and release layout. |
| `karaoke_album.py` | No upstream-code import | Sanitized manifest and path model. |
| `karaoke_language.py` | No upstream-code import | Language normalization and validated-profile gates. |
| `build_karaoke_wide_artwork.py` | No upstream-code import | Pillow artwork construction. |
| `render_vinyl_karaoke.py` | No upstream-code import | Vinyl visual layer construction. |
| `inspect_karaoke_media.py` | No upstream-code import | Encoded-media and render-metadata inspection. |
| `transcode_karaoke_av1.py` | No upstream-code import | FFmpeg metadata transcoding and verification. |
| `prepare_karaoke_msst_vocals.py` | No upstream-code import | Optional external MSST evidence preparation. |
| `package_karaoke_numbered_archives.py` | No upstream-code import | Numbered release archives. |
| `karaoke_release_snapshot.py` | No upstream-code import | Release-file snapshots. |
| `pitch_shift_audio.py` | No upstream-code import | Complete-mix pitch shifting and its verification report. |

The audio probe dynamically imports the application's GUI, persistence, and
audio-loading modules. The installer and environment checker operate on an
existing checkout without importing application code. The authoritative list
is `integration/strangeutagame/dependency-manifest.json`.

## Repository layout and tests

```text
.
├── SKILL.md
├── LICENSE
├── NOTICE.md
├── THIRD_PARTY_NOTICES.md
├── agents/                  # packaging metadata
├── examples/                # generic private-data examples
├── integration/strangeutagame/
│   ├── dependency-manifest.json
│   ├── requirements/
│   └── scripts/
├── references/
├── scripts/
│   ├── check_karaoke_environment.py
│   ├── check_sug_compatibility.py
│   ├── install_strangeutagame_integration.py
│   ├── open_editable_project_with_audio_probe.py
│   └── pitch_shift_audio.py
└── tests/
```

```powershell
python -m unittest discover -s scripts -p "test_*.py" -v
python -m unittest discover -s tests -p "test_*.py" -v
```

These packaging and safety tests do not claim that a full media render succeeds
without private fixtures. Before production, run the environment checker, all
command help smoke tests, an authorized short preview, and the release gates.

## License and rights

The included `LICENSE` file states GPL-3.0-only for this repository's code and
documentation. See [NOTICE.md](NOTICE.md) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The repository is a later-
developed integration for
[karaoke-studio/StrangeUtaGame](https://github.com/karaoke-studio/StrangeUtaGame),
whose upstream repository declares GPL-3.0. This repository is not the upstream
application and does not redistribute that application. Users must secure the
rights for recordings, lyrics, artwork, fonts, models, and distribution.
FFmpeg's terms depend on its build configuration; review its legal page before
distribution.
