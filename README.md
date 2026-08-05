# Karaoke AV1 Video Production Skill

A Codex skill for producing, rebuilding, reviewing, and packaging karaoke or lyric videos with traceable subtitle timing and verified AV1 4:2:0 delivery.

## What it covers

- Semantic lyric segmentation, cue pairing, lane behavior, and held-syllable sweep review
- Japanese ruby reading and lexical word-boundary verification
- Editable-project, ASS/report, render, and packaged-artifact parity
- Guarded StrangeUtaGame project opening with attached-audio evidence
- Wide-layout typography, CJK fit, cover-derived highlight colours, and artwork continuity
- AV1 4:2:0 encoding, probing, promotion, rollback, and archive checks
- Rights, privacy, and generation-identity reporting gates

The skill starts from authorized media, lyrics, subtitles, fonts, and audio. It does not perform lyric transcription, vocal separation, voice cloning, or music generation.

## Install

Clone the repository into the Codex skills directory:

### Windows PowerShell

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

### macOS or Linux

```bash
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git ~/.codex/skills/karaoke-av1-video-production
```

Because the repository is private, GitHub authentication is required when cloning it.

## Use

Invoke the skill explicitly when needed:

```text
$karaoke-av1-video-production
```

The complete workflow is in [SKILL.md](SKILL.md). Detailed references are loaded only when their gates apply.

## Repository layout

```text
.
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── av1-420-commands.md
│   ├── batch-release-gates.md
│   └── subtitle-timing-quality.md
└── scripts/
    ├── open_editable_project_with_audio_probe.py
    └── test_open_editable_project_with_audio_probe.py
```

## Script test

```bash
python -m unittest discover -s scripts -p "test_*.py" -v
```

The editor probe is designed for private local evidence. It does not contain fixed usernames, media paths, credentials, or song metadata. Runtime reports, editable projects, subtitles, media, caches, and generated artifacts are excluded by `.gitignore`.
