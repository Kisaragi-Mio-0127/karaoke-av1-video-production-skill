from __future__ import annotations

from scripts.karaoke_common.layout import STANDARD_LAYOUT
from scripts.karaoke_common.visuals import VISUAL_STYLES, build_visual_filter_graph
from scripts.karaoke_direct_album_planning import select_visual_styles


def _graph(style: str) -> str:
    return build_visual_filter_graph(
        visual_style=style,
        subtitle_filter="null",
        start_seconds=0.0,
        duration_seconds=3.0,
        program_duration_seconds=10.0,
        layout=STANDARD_LAYOUT,
        vinyl_motion="static",
        spectrum_color="#E19E84",
        progress_color="#84C7E1",
    )


def test_spectrum_line_graph_draws_40_point_polyline_and_vertical_stems():
    graph = _graph("spectrum-line")

    assert "asplit=2[a][specaudio]" in graph
    assert "showfreqs=s=38x220:r=30:mode=bar" in graph
    assert "format=gray16le,scale=38:1:flags=area" in graph
    assert "pad=40:1:1:0:color=black" in graph
    assert "scale=4266:880:flags=bilinear,crop=4160:880:53:0" in graph
    assert "st(0\\,940-p(X\\,Y)/64.25)" in graph
    assert "st(1\\,940-p(107\\,Y)/64.25)" in graph
    assert "st(2\\,940-p(4052\\,Y)/64.25)" in graph
    assert "abs((ld(1)-876)*X-107*(Y-876))" in graph
    assert "abs((876-ld(2))*(X-4052)-107*(Y-ld(2)))" in graph
    assert "lte(abs(X-round(X*39/4159)*4159/39)\\,7)" in graph
    assert "gt(X\\,7)*lt(X\\,4152)" in graph
    assert "gte(Y\\,ld(0))" in graph
    assert "scale=1040:220:flags=lanczos,format=gray" in graph
    assert "lut=y='if(lte(val\\,16)\\,0\\,val)'" in graph
    assert graph.count(
        "drawbox=x=0:y=290:w=1168:h=70:color=black:t=fill"
    ) == 2
    assert "[areafill]" not in graph
    assert "[bgclip][lineouter]overlay=736:226" in graph
    assert "[lineglow][linecore]overlay=800:296" in graph
    assert "drawbox=x=800:y=516:w=1040:h=3" not in graph
    assert "drawgrid=" not in graph
    assert "[spectrumbars]" not in graph


def test_existing_bar_spectrum_graph_remains_discrete():
    graph = _graph("spectrum")

    assert "showfreqs=s=80x220:r=30:mode=bar" in graph
    assert "drawgrid=width=13:height=220" in graph
    assert "mode=line" not in graph


def test_visual_style_batch_selectors_preserve_both_and_offer_all():
    assert VISUAL_STYLES == ("vinyl", "spectrum", "spectrum-line")
    assert select_visual_styles("both") == ("vinyl", "spectrum")
    assert select_visual_styles("all") == VISUAL_STYLES
    assert select_visual_styles("spectrum-line") == ("spectrum-line",)
