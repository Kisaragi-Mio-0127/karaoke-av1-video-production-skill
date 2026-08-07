# Singer Identity And Secondary Overlay Gates

[简体中文](singer-overlays.zh-CN.md) | English

Use these gates for any multi-singer SUG project, including Japanese projects with ruby. Keep singer identity, subtitle colour, role placement, and ruby boundaries explicit and independently checkable.

## Resolve singer identity

- Require an explicit SUG `singer_id` for every singer. Resolve each visible character with this precedence: character-level `singer_id`, sentence-level `singer_id`, then the explicit project default singer.
- Apply the resolved singer colour consistently to the active `Main`, `Glow`, cue, and top secondary subtitle layers (`Secondary`/`SecondaryGlow`). Inactive or unhighlighted text remains white.
- Let role metadata choose placement, not singer identity. Never infer a singer from lyric wording, role labels embedded in the lyric text, punctuation, audio names, or visual appearance. If no explicit project default can resolve a missing identity, stop before rendering.
- Verify the resolved identity and colour at character, sentence, and project fallback levels in the SUG, ASS/report, and representative frames.

## Route secondary roles

- Route explicit `opera`, `harmony`, and `secondary` roles to a dedicated top-centred overlay. Keep it independent of the main lyric lanes, main cues, and ruby events; an active top block must not suppress the ordinary bottom-lane preload.
- Use the top safe band `y=0..96` with the default anchor `y=12`. Use a default font size of `60 px` and reduce a long line only to a minimum of `36 px`; the actual outline/glow reserve extends through `y=107`.
- Apply the same resolved singer colour to the top secondary subtitle and its glow. Keep inactive or unhighlighted top text white. In `wide-layout-v6/top-secondary-clearance`, use actual title ink bounds, keep at least `16 px` between the title ink and the secondary reserve, and place the title label/title/artist at `y=120/155/220`.

## Reject cross-singer ruby

- Resolve singer identity before validating every ruby span. A ruby chain must resolve to one singer after character-level and sentence-level fallback.
- Reject a span when its linked surface characters resolve to different singer IDs. Do not split or silently reassign the ruby during rendering; repair the canonical SUG and review it again.
- Keep the renderer read-only for ruby and require the same cross-singer check in the one-click preflight, final render, and lower-level renderer gate.

## Keep one release contract

- Run the same singer-resolution, colour, top-overlay, ruby, container, and diagnostic gates from the one-click workflow and the underlying renderer. A convenience entry point must not bypass a lower-level release check.
- Keep MP4 as the default delivery. Create MKV only after an explicit opt-in and a verified lossless source; do not create an implicit companion.
- Keep full decode off by default and enable it only through an explicit full-decode selection; an unperformed full decode is not a release failure. Japanese pronunciation validation defaults to non-blocking `optional`; `required` and `off` are explicit choices.
