"""ASS subtitle animation shared by the karaoke render entry points."""

from __future__ import annotations

from scripts.karaoke_timing import ms_to_ass_time

SUBTITLE_STYLES = ("sweep", "jump")
JUMP_HEIGHT_PX = 18
JUMP_DURATION_MS = 320


def jump_glyph_events(
    text: str,
    *,
    x: int,
    y: int,
    onset_ms: int,
    release_ms: int,
    event_start_ms: int,
    event_end_ms: int,
    font_size: int,
    outline_px: int,
    glow_blur: int,
    color_ass: str,
    glow_style: str,
    main_style: str,
    glow_layer: int,
    main_layer: int,
) -> list[str]:
    """Highlight at onset and move up/down once, keeping the final colour.

    ASS supports one move per event, so the ascent and descent are separate
    events. All boundaries share the ASS centisecond grid, including short
    notes and display windows that start partway through a note.
    """

    start = max(0, event_start_ms // 10 * 10)
    end = max(0, event_end_ms // 10 * 10)
    onset = onset_ms // 10 * 10
    duration = max(10, min(JUMP_DURATION_MS, (release_ms // 10 * 10) - onset))
    rise = max(10, (duration * 2 // 5) // 10 * 10)
    peak = onset + rise
    landed = onset + duration
    height = min(JUMP_HEIGHT_PX, round(font_size * 0.18), max(0, y - outline_px))
    if duration <= 10:
        height = 0
    phases = (
        (start, onset, y, y, False),
        (onset, peak, y, y - height, True),
        (peak, landed, y - height, y, True),
        (landed, end, y, y, True),
    )
    result: list[str] = []
    for phase_start, phase_end, y1, y2, highlighted in phases:
        visible_start = max(start, phase_start)
        visible_end = min(end, phase_end)
        if visible_end <= visible_start:
            continue
        phase_duration = phase_end - phase_start
        from_y = y1 + (y2 - y1) * (visible_start - phase_start) / phase_duration
        to_y = y1 + (y2 - y1) * (visible_end - phase_start) / phase_duration
        position = (
            f"\\pos({x},{from_y:g})"
            if from_y == to_y
            else f"\\move({x},{from_y:g},{x},{to_y:g},0,{visible_end - visible_start})"
        )
        fade_in = min(80, visible_end - visible_start) if visible_start == start else 0
        fade_out = min(120, visible_end - visible_start) if visible_end == end else 0
        delay_cs = 0 if highlighted else (visible_end - visible_start) // 10
        common = (
            f"\\an8{position}\\fs{font_size}\\bord{outline_px}"
            f"\\1c{color_ass}\\2c&H00FFFFFF\\k{delay_cs}\\k0\\fad({fade_in},{fade_out})"
        )
        for layer, style, extra in (
            (glow_layer, glow_style, f"\\1a&H50&\\2a&H70&\\blur{glow_blur}"),
            (main_layer, main_style, ""),
        ):
            result.append(
                f"Dialogue: {layer},{ms_to_ass_time(visible_start)},"
                f"{ms_to_ass_time(visible_end)},{style},,0,0,0,,"
                f"{{{common}{extra}}}{text}"
            )
    return result
