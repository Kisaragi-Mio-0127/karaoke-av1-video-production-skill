# Copyright and modification notice

Unless a file says otherwise, this repository's code and documentation are
distributed under **GPL-3.0-only**. Copyright remains with the respective
authors and contributors.

The files under `integration/strangeutagame/scripts/` are modified copies from
[karaoke-studio/StrangeUtaGame](https://github.com/karaoke-studio/StrangeUtaGame),
project version 1.2.6. The source working tree was based on commit
`d1b121a53c8b9167986933c21afa1d1c9d8a0355`; the karaoke scripts were untracked
working-tree additions at packaging time and therefore do not have individual
upstream commit identities.

Changes made for this repository on 2026-08-05 include:

- removal of real album names, lyrics, media hashes, cover URLs, and local paths;
- explicit manifest/environment configuration instead of import-time private data;
- external JSON for song-specific display and ruby decisions;
- opt-in, constrained network access;
- a guarded StrangeUtaGame installer and release-safety tests.

The integration file list is maintained in
`references/strangeutagame-integration.md`. See `THIRD_PARTY_NOTICES.md` for
runtime components that are referenced but not redistributed.

This repository does not redistribute recordings, lyrics, fonts, cover art,
FFmpeg/Rubber Band binaries, drivers, model files, or rendered media. Users are
responsible for their licenses, service terms, and redistribution rights.
