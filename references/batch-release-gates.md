# Batch Release Gates

[简体中文](batch-release-gates.zh-CN.md) | English

Use these gates when encoding, promoting, or packaging multiple songs or delivery profiles together.

## Freeze the generation

Before full encoding, record a parameter fingerprint covering source files, timing overrides, renderer, report and test identities, encoder, pixel format, quality control, preset, audio, container, fonts, lyric and ruby sizes, spacing, render options, timing evidence, and delivery profile. Keep song counts, cue counts, quality values, font sizes, and filenames in project configuration rather than the generic skill.

Batch rendering never runs MMS. If the fixed-path `timing_overrides` artifact exists, automatically consume its existing visual-release overrides and record the artifact identity; do not create a new override during the batch run. The renderer does not validate MMS provenance, so validate the artifact's source, generation identity, and review status before encoding or promotion. If the artifact is absent, do not invoke MMS implicitly.

## Isolated staging

- Encode in a dedicated staging directory outside accepted deliverables.
- Require successful renderer and encoder exits; reject partial files, stale outputs, unverified profiles, and mixed generation identities.
- Compare per-track and total sizes with the previous accepted generation. Treat unusual changes as investigation signals rather than quality proof.
- Treat complete null decoding as optional and off by default; enable it only after an explicit selection. When performed, map the intended streams and record actual exit codes.
- Extract boundary frames for cues, longest phrases, ruby exceptions, and release-overlap conflicts.

## Multi-file promotion

Windows multi-file promotion is not transactional. Record staging, destination, backup, before/after hashes, generation ID, and status for every file. Resolve and constrain all paths before moving files, promote one item at a time, and mark completion only after probing and hashing the final destination. On failure, stop and restore completed items in reverse order.

Generate delivery names from one manifest rule covering track number, display title, platform-safe punctuation, and extension. Reuse the same naming rule in the renderer, finalizer, playlist, archive, and tests.

## Archives and compression

Package an explicit profile and allowlist instead of a broad directory wildcard. Read back every member, verify CRC or format integrity, and match media members to accepted output hashes and manifest identities. Record the archive hash, member list, sizes, media hashes, and generation ID.

Inspect the actual compression method. For already-compressed AV1, HEVC, AAC, or MP4 inputs, compare representative sizes and elapsed time before selecting a compression level. Prefer widely compatible ZIP DEFLATE for ordinary delivery and regenerate the root release manifest from the final archive.

## Cleanup and evidence

Clean generation-owned partial files, preview frames, isolated caches, and confirmed redundant rollback data only after final media, archives, member hashes, and the root manifest pass. Retain the latest useful rollback until the release is accepted, then rescan for partial outputs and report retained items.

The release report records the parameter fingerprint, test result, staging inventory, optional decode status, sampled windows, frame checks, size-drift decision, promotion manifest, rollback status, archive allowlist, read-back results, internal hashes, and final output hashes.
