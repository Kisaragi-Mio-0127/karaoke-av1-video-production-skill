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


def test_spectrum_line_graph_draws_contour_and_fills_to_zero_baseline():
    graph = _graph("spectrum-line")

    assert "asplit=3[a][fillaudio][lineaudio]" in graph
    assert "showfreqs=s=1040x220:r=30:mode=line" in graph
    assert "showfreqs=s=1040x220:r=30:mode=bar" in graph
    fill_mask = graph.split(";[fillaudio]", 1)[1].split("[fillmask]", 1)[0]
    assert fill_mask.count("erosion=coordinates=90") == 2
    assert fill_mask.count("dilation=coordinates=90") == 2
    assert "drawbox=x=0:y=216:w=1040:h=4:color=black:t=fill" in fill_mask
    line_mask = graph.split(";[lineaudio]", 1)[1].split("[linecoremask]", 1)[0]
    assert "gblur=" not in line_mask
    assert "dilation=" not in line_mask
    assert ",scale=" not in line_mask
    assert "drawbox=x=0:y=216:w=1040:h=4:color=black:t=fill" in line_mask
    assert graph.count(
        "drawbox=x=0:y=286:w=1168:h=74:color=black:t=fill"
    ) == 2
    assert "[bgclip][areafill]overlay=800:296" in graph
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
