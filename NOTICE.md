# Copyright and modification notice

Unless a file says otherwise, this repository's code and documentation are
distributed under **GPL-3.0-only**. Copyright remains with the respective
authors and contributors.

The files under `integration/strangeutagame/scripts/` are later-developed
integration scripts, not files from the Git history of
[karaoke-studio/StrangeUtaGame](https://github.com/karaoke-studio/StrangeUtaGame).
They were developed and used in a working tree based on StrangeUtaGame 1.2.6,
commit `d1b121a53c8b9167986933c21afa1d1c9d8a0355`, and were untracked additions in
that working tree before packaging. Some scripts directly import tracked
StrangeUtaGame application modules, some depend on those scripts transitively,
and others are release helpers with no upstream-code import. The machine-readable
classification is `integration/strangeutagame/dependency-manifest.json`.

Changes made for this repository on 2026-08-05 include:

- removal of real album names, lyrics, media hashes, cover URLs, and local paths;
- explicit manifest/environment configuration instead of import-time private data;
- external JSON for song-specific display and ruby decisions;
- opt-in, constrained network access;
- a guarded StrangeUtaGame installer and release-safety tests.

This repository does not redistribute the StrangeUtaGame application package.
The integration file list and dependency boundary are maintained in the README
and `integration/strangeutagame/dependency-manifest.json`. See
`THIRD_PARTY_NOTICES.md` for runtime components that are referenced but not
redistributed.

This repository does not redistribute recordings, lyrics, fonts, cover art,
FFmpeg/Rubber Band binaries, drivers, model files, or rendered media. Users are
responsible for their licenses, service terms, and redistribution rights.
