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

The repository is currently private, so GitHub authentication is required.
Invoke it in Codex with:

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

## Repository layout

```text
.
├── SKILL.md
├── LICENSE
├── NOTICE.md
├── agents/
├── examples/
├── integration/strangeutagame/
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
