# Wide Visual Templates

[简体中文](wide-visual-templates.zh-CN.md) | English

This is the single source of truth for the current wide-layout geometry. Both
`karaoke-av1-video-production` and
`chinese-english-karaoke-production` link here; do not copy these constants
into either `SKILL.md`, another command reference, or a language adapter.

Read this file before changing composition, subtitle placement, secondary
overlays, vinyl artwork, or spectrum rendering. Re-check the implementation
when the renderer or canvas changes.

## Template selection

- Select exactly one `--visual-style`: `vinyl` or `spectrum`.
- `vinyl` keeps the record rotating and generates its record asset inside the
  current run's output directory.
- `spectrum` omits `--vinyl` and must not probe, generate, pass, or report a
  vinyl asset.
- Never combine both effects in one output. The AV1 batch entry's `both`
  option creates two independent outputs, not a combined frame.

## Current composition

- Composition identifier: `wide-layout-v7/cover-palette`.
- Canvas: `1920x1080`.
- The album card, card footer, and lower subtitle panel stay visible. Vinyl
  shows the rotating record; spectrum replaces that region with the spectrum
  and progress display. The outer right panel and compact dark vinyl backplate
  stay absent.
- Required no-panel report fields:
  `right_panel_visible=false`, `outer_right_panel_visible=false`,
  `vinyl_backplate_present=false`, and
  `vinyl_backplate_preserved=false`.

## Artwork geometry

- Lower subtitle panel: `(x1,y1,x2,y2)=(20,576,1900,1050)`.
- Vinyl album card: `(x,y,width,height)=(40,30,340,402)`.
- Spectrum album card: `(x,y,width,height)=(40,30,460,522)`.
- Vinyl title visual left edge: `430`.
- Spectrum title, spectrum, and progress visual left edge: `800`.
- Vinyl footer bottom padding: `12`.
- Title label/title/artist baselines: `y=120/155/220`, positioned by actual
  ink bounds.

## Subtitle and secondary geometry

- Wide upper lane: main `y=660`, ruby anchor `y=625`.
- Wide lower lane: main `y=870`, ruby anchor `y=835`.
- Main-lane separation: `210 px`; ruby-to-main anchor gap: `35 px`.
- Japanese/Chinese wide main font target: `108 px`; shared ruby font target:
  `51 px`; cue text target: `39 px`.
- English wide main font target: `96 px`; exceptional minimum: `54 px`.
- English word runs use `0 em` added intra-word tracking, `0.85` Pillow-to-
  libass advance positioning, and a `0.18 em` total word-gap target.
- Secondary roles (`opera`, `harmony`, `secondary`) use a centred top overlay:
  content-safe band `y=0..96`, anchor `y=12`, default font `60 px`, minimum
  long-line font `36 px`, and outline/glow reserve through `y=107`.
- Keep at least `16 px` between title ink and the secondary reserve.
- Keep secondary content independent from main lanes, cues, and ruby. Reject a
  ruby span whose resolved characters contain more than one singer.

## Spectrum geometry

- Spectrum drawing rectangle: `(x,y,width,height)=(800,290,1040,220)`.
- Spectrum baseline: `y=516`.
- Clip-safe rectangle: `(x,y,width,height)=(736,226,1168,348)`.
- Horizontal glow padding: `64 px`.
- Top and bottom glow padding: `56 px` each.
- Top and bottom bar clearance: `8 px` each.
- Bar count: `80`; corner radius: `3 px`; soft-edge sigma: `0.8`.
- Peak hold: enabled; decay `0.975`; half-life `0.91 s`.
- Progress track: `(x,y,width,height)=(800,548,1040,6)` with a circular
  `20 px` indicator; `show_time=false`.
- Verify bars, glow, title alignment, peak response, and the progress endpoint
  in original-resolution frames, not only in JSON metadata.

## Automatic layout and low-level checks

The one-click wrappers generate the current composition inside the new output
directory. They use the selected cover, derive a background when one is not
explicitly supplied, and create a vinyl asset only for `vinyl`. An explicit
`--composition` is an advanced override and must still pass the current layout
identifier and geometry gates; it must not silently reintroduce an old style.

Use the following low-level commands only when inspecting the artwork builder
or renderer directly.

Build the composition with the real project CLI:

```powershell
uv run --no-sync python scripts/build_karaoke_wide_artwork.py `
  --background <background> --cover <cover> `
  --font-regular <regular-font> --font-bold <bold-font> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style <vinyl-or-spectrum> --output <composition-png>
```

Render a representative preview with the matching style:

```powershell
uv run --no-sync python scripts/karaoke_review_preview.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --composition <composition-png> --fonts-dir <fonts-dir> `
  --font-file <main-font> --output <new-output-mp4> `
  --ass-output <new-output-ass> --report-output <new-report-json> `
  --start <seconds> --duration <seconds> --layout wide `
  --visual-style <vinyl-or-spectrum>
```

For a low-level `vinyl` renderer check, add the vinyl produced by the same
artwork run and record its identity. For `spectrum`, omit it. Use new output,
ASS, and report paths for every review run; keep accepted artifacts and
rollback copies separate.

## Acceptance evidence

Require the composition/report layout identifier to match this contract.
Inspect title, first lyric, longest line, dense timing, secondary overlay,
active spectrum, progress, and ending frames. For vinyl inspect at least four
rotation phases and reject seams or sweeping partial arcs. For spectrum verify
real-time response, peak decay, rounded bars, unclipped glow, aligned title and
progress boundaries, and safe endpoint behavior.
