# Wide Visual Templates

[简体中文](wide-visual-templates.zh-CN.md) | English

Select exactly one right-side visual effect for each wide render. Use the same shared production scripts for both choices; do not fork a song-specific renderer.

## Choose the template

- Use `vinyl` for the rotating-record layout. Pass `--visual-style vinyl` to both scripts and provide `--vinyl` to the preview renderer.
- Use `spectrum` for the glowing real-time spectrum layout. Pass `--visual-style spectrum` to both scripts and omit `--vinyl`; the current CLI ignores an unnecessary vinyl argument instead of rejecting it.
- Never combine the rotating record and spectrum in one output. Preserve accepted variants under distinct filenames.
- Render and inspect a short representative preview before a full encode whenever the template, artwork renderer, spectrum behavior, or layout constants change.

## Use the shared scripts

From the StrangeUtaGame repository root, build the static background/composition layer with `scripts/build_karaoke_wide_artwork.py`. The vinyl itself keeps rotating and is a separate current derived asset; regenerate it with the current renderer on every formal or test run:

```powershell
uv run --no-sync python scripts/build_karaoke_wide_artwork.py `
  --background <background> --cover <cover> `
  --font-regular <regular-font> --font-bold <bold-font> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style <vinyl-or-spectrum> --output <composition-png>
```

Render with `scripts/karaoke_review_preview.py` and the same `--visual-style`. Pass `--layout wide`; omitting it selects the standard subtitle lanes and standard vinyl placement. Pass the exact post-mix audio source intended for delivery. The same trimmed `--audio` input drives the spectrum and muxed audio, although the MP4 audio is re-encoded. Use a new output, ASS, and report path for every spectrum run because spectrum mode refuses to overwrite them. Vinyl mode can overwrite video, ASS, and report targets, and the artwork builder can overwrite its PNG and JSON; use new paths or rollback copies for accepted artifacts.

```powershell
uv run --no-sync python scripts/karaoke_review_preview.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --composition <composition-png> --fonts-dir <fonts-dir> `
  --font-file <main-font> --output <new-output-mp4> `
  --ass-output <new-output-ass> --report-output <new-report-json> `
  --start <seconds> --duration <seconds> --layout wide `
  --visual-style <vinyl-or-spectrum> <vinyl-only-arguments>
```

For `vinyl`, replace `<vinyl-only-arguments>` with `--vinyl <current-vinyl-png>` and record the exact `vinyl_sha256`; the renderer must receive that path explicitly and must not silently reuse a canonical/old `vinyl.png`. For `spectrum`, omit it and optionally add `--spectrum-color RRGGBB --progress-color RRGGBB`. If timing overrides are used, pass `--timing-overrides <json>` and `--song-id <id>` together. The default full-program AV1 4:2:0 direct-render profile is 1920x1080 at 30 fps, `yuv420p`, BT.709, AV1 NVENC CQ38, `preset p7`, `tune hq`, VBR, full-resolution multipass, lookahead 32, spatial and temporal AQ, AQ strength 8, and GOP 240 after a successful hardware probe. The default compatibility MP4 uses AAC-LC 320k and is the only output for ordinary tests/re-renders. MKV is opt-in only: pass `--lossless-output <new-lossless-output-mkv>` (or use a workflow's explicit `--lossless-companion`) only after probing a FLAC or PCM WAV source; reject MP3/AAC. Do not show elapsed time or playback-control buttons.

## Current composition contract

The current wide composition uses `wide-layout-v5/no-right-panels`. It removes both the extra outer right-panel overlay and the compact dark backplate behind or below the rotating record. The record still rotates, and the album card, card footer, and lower subtitle panel remain visible. The spectrum variant does not reintroduce the removed vinyl-region background frame.

## Preserve the visual contract

- Treat the following values as the current 1920×1080 StrangeUtaGame wide-template constants, not universal karaoke coordinates. Re-read the scripts when the renderer or canvas changes.
- Keep the common lower subtitle backdrop at corner coordinates `(x1,y1,x2,y2)=(20,576,1900,1050)`; its top edge is `y=576` and it remains in the composition. Subtitle anchors come from `--layout`, not from this rectangle.
- For `vinyl`, use album-card geometry `(x,y,width,height)=(40,30,340,402)`, footer bottom padding `12`, and title block visual left edge `430`.
- For Japanese wide subtitles, the upper and lower main-lyric anchors are `y=660` and `y=870` respectively.
- In the composition report, `right_panel: null`/`right_panel_visible: false` and `outer_right_panel: null`/`outer_right_panel_visible: false` confirm that no outer right-panel overlay is present. `vinyl_backplate: null`, `vinyl_backplate_present: false`, and compatibility `vinyl_backplate_preserved: false` confirm that the compact backplate is also absent.
- For `spectrum`, use album-card geometry `(40,30,460,522)`, title/spectrum/progress visual left edge `800`, spectrum geometry `(x,y,width,height)=(800,290,1040,220)`, baseline `y=516`, and progress geometry `(800,548,1040,6)` with a 20 px circular indicator. Keep the clip-safe geometry `(x,y,width,height)=(736,226,1168,348)`, 64 px horizontal glow padding, 56 px top/bottom glow padding, and 8 px top/bottom bar clearance so upper peaks and lower glow are not clipped.
- Align visible title ink, spectrum bars, and progress track by their reviewed visual boundary, not only by a text draw origin or glow-layer canvas.
- Target 80 visually rounded bars with rounded tops and bottoms, horizontal glow padding, top/bottom clearance and glow padding, recent-peak hold, and a circular progress indicator. Confirm the rounding, upper/lower safe margins, and endpoint behavior in original-resolution frames rather than relying only on reported constants. Keep side, top, and bottom glow inside padded intermediate layers so it fades naturally instead of clipping at the spectrum rectangle.
- Use the approved cover-derived primary colour for active bars and glow. Prefer a reviewed cover-derived secondary/accent for the progress track; if none exists, record the fallback.
- Require `--report-output` and verify its `visual_style`, colour choices, spectrum geometry, bar count/radius, glow padding, peak-hold settings, progress geometry, and `show_time: false`. Separately compute and record SHA-256 identities for the composition, post-mix audio, optional vinyl, ASS, report, and output because the current scripts do not add all input identities automatically.

## Verify before promotion

Use the renderer only to create artifacts. Perform frame inspection, media probing, promotion, destination re-probing, and rollback retention as separate gates. Inspect the intro, an active low-energy passage, an active peak, a dense lyric frame, and the outro. For vinyl, also inspect at least four rotation phases and verify no seam or sweeping colour sector. For spectrum, verify real-time response, peak decay, rounded bar bottoms, unclipped side/bottom glow, title/spectrum alignment, and safe progress endpoint behavior.
