# Batch Release Gates

[简体中文](batch-release-gates.zh-CN.md) | English

Use these gates when encoding, promoting, or packaging multiple songs or delivery profiles.

## Freeze The Generation

Before full encoding, record a parameter fingerprint containing:

- Source, timing override, renderer, render-report, and test identities or hashes.
- Encoder, pixel format, CQ/CRF, preset, profile, audio settings, container, font, main/ruby sizes, spacing, and renderer options.
- Selected timing evidence lane and delivery profile.

Do not hardcode one project's song counts, cue counts, CQ values, sizes, or filenames into this skill.

## Isolated Staging

- Encode into a dedicated staging directory outside accepted deliverables.
- Require renderer and encoder processes to finish before promotion.
- Fail when partial files, stale outputs, unverified profiles, or mixed generation identities remain.
- Compare per-song and total output sizes with the previous accepted release. Pause on unexplained order-of-magnitude or asymmetric drift; file size is a signal, not a quality proof.
- Treat full-output null decoding as an optional diagnostic, not a default batch gate. Run it only on user request or when probe, mux, transport, or corruption evidence warrants it. If any decode is executed, map the intended streams and record every real exit code; never synthesize success for an unperformed diagnostic.
- Extract boundary frames for cues, longest phrases, ruby exceptions, and release-overlap conflicts.

## Multi-File Promotion

Windows multi-file promotion is not one transaction.

- Write a promotion manifest with staged path, target path, backup path, pre/post hash, generation id, and promotion status for every file.
- Resolve and constrain all paths before moving. Keep staged and target files on the same volume when atomic per-file replacement is required.
- Promote one file at a time, recording completion only after final-path probe and hash verification.
- On failure, stop and restore completed entries in reverse order. Re-probe and re-hash restored targets.
- Do not report the batch as promoted when only some entries succeeded.

### Delivery Names

- Derive one canonical delivery basename per track from the manifest: track number, display title, platform-safe punctuation, and extension. Reuse that function in the renderer, finalizer, playlist, archive builder, and tests; keep artifact slugs for internal source identities only.
- When the published delivery folder is user-visible, require its media basenames to match the archive media basenames exactly. A staging or internal source name may differ, but its manifest identity and content hash must still match.
- Configure the final basename before rendering so direct render reports record the delivered path. If an accepted file is renamed later, regenerate or migrate every path-bearing report before release.
- Treat ASCII-to-platform-safe punctuation conversion as a deterministic manifest rule. Do not maintain separate handwritten title overrides in the renderer and packager.

## Archive Gate

When producing ZIP or another archive:

- Select the intended lane and profile explicitly; do not package by broad directory glob.
- Build from a whitelist with deterministic path order and no partials, logs, private probes, sources, credentials, or stale media.
- Read every archive member fully and verify CRC or format integrity.
- Compare the hash of each packaged media file with the accepted promoted artifact.
- Match packaged media by manifest identity such as lane, profile, track number, and content hash. Also enforce literal basename equality when the source is the published user-visible delivery folder; allow different names only for private staging or internal sources.
- Record archive hash, member list, member sizes, media hashes, and generation id before release.

### Compression Selection

- Inspect the actual member compression method; a `.zip` extension may still contain only stored, uncompressed members.
- For already-compressed AV1, HEVC, AAC, or MP4 inputs, benchmark a representative large member at a moderate and maximum compatible level before choosing. Prefer the moderate level when the maximum level saves only a negligible amount, and record the measured bytes, ratio, and elapsed time.
- Prefer broadly compatible ZIP DEFLATE for ordinary delivery. Use stored members only when packaging speed or byte-for-byte media access is explicitly more important than archive size; use less compatible methods only when the recipient and extraction tools are known.
- Record the archive algorithm, level, selection reason, total uncompressed member bytes, compressed payload bytes, final archive bytes, and ratio. Do not describe a numeric level as high or maximum without naming the algorithm.
- Generate the archive sidecar checksum as part of the same packager transaction, then regenerate any root release manifest from the final archive generation. Recompute every listed hash; a valid archive with stale checksum files is not a complete release.

## Cleanup Gate

- After final-path media, archive, member hashes, and root manifests pass, remove only generation-owned partial files, preview frames, isolated test caches, and superseded rollbacks whose targets were resolved inside their intended roots.
- Keep the newest useful rollback until the user accepts the release. Preserve unknown or older historical rollbacks unless their ownership and redundancy are established.
- Re-scan for partial outputs after cleanup and report any intentionally retained rollback or diagnostic directory.

## Evidence Report

Report the parameter fingerprint, clean test result, staging inventory, whether optional decode diagnostics were performed, real exit codes for every executed full or sampled decode, sampled windows, key-frame review, size-drift decision, promotion manifest, rollback status, archive whitelist, CRC/readback result, internal hashes, and final artifact hash. Never synthesize a successful exit code for an unperformed diagnostic.
