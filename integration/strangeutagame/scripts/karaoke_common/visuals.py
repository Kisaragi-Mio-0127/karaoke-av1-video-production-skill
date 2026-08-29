"""Build FFmpeg filter graphs for the supported karaoke visual styles."""

from __future__ import annotations

import re

from scripts.karaoke_common.layout import SubtitleLayout

VISUAL_STYLES = (
    "vinyl",
    "spectrum",
    "spectrum-line",
    "spectrum-mirror",
    "spectrum-dots",
    "spectrum-waterfall",
)
VINYL_MOTIONS = ("rotate", "static")


def normalize_rgb_hex(value: str, *, name: str) -> str:
    """Return an uppercase six-digit RGB value without the leading hash."""

    color = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
        raise ValueError(f"invalid {name}: {value!r}")
    return color.upper()


def build_visual_filter_graph(
    *,
    visual_style: str,
    subtitle_filter: str,
    start_seconds: float,
    duration_seconds: float,
    program_duration_seconds: float,
    layout: SubtitleLayout,
    vinyl_motion: str,
    spectrum_color: str,
    progress_color: str,
) -> str:
    """Build the style-owned FFmpeg graph without running the encoder."""

    if visual_style not in VISUAL_STYLES:
        raise ValueError(f"unsupported visual style: {visual_style}")
    if vinyl_motion not in VINYL_MOTIONS:
        raise ValueError(f"unsupported vinyl motion: {vinyl_motion}")

    start = max(0.0, float(start_seconds))
    duration = max(0.1, float(duration_seconds))
    end = start + duration
    program_duration = max(end, float(program_duration_seconds))
    color = normalize_rgb_hex(spectrum_color, name="spectrum color")
    progress = normalize_rgb_hex(progress_color, name="progress color")

    if visual_style == "vinyl":
        vinyl_filter = (
            "format=rgba,rotate=2*PI*t/8:ow=iw:oh=ih:"
            "fillcolor=black@0:bilinear=1"
            if vinyl_motion == "rotate"
            else "format=rgba"
        )
        return (
            "[0:v]format=rgba[bg];"
            f"[1:v]scale={layout.vinyl_size}:{layout.vinyl_size}:flags=lanczos,"
            f"{vinyl_filter}[vinyl];"
            f"[bg][vinyl]overlay={layout.vinyl_x}:{layout.vinyl_y}:format=auto[scene];"
            f"[scene]{subtitle_filter},trim=start={start:.3f}:end={end:.3f},"
            "setpts=PTS-STARTPTS[v];"
            f"[2:a]atrim=start={start:.3f}:end={end:.3f},"
            "asetpts=PTS-STARTPTS[a]"
        )

    if visual_style == "spectrum-line":
        spectrum_scene = (
            f"[0:v]format=rgba,trim=start={start:.3f}:end={end:.3f},"
            "setpts=PTS-STARTPTS[bgclip];"
            f"[1:a]atrim=start={start:.3f}:end={end:.3f},"
            "asetpts=PTS-STARTPTS,asplit=2[a][specaudio];"
            "[specaudio]aformat=channel_layouts=mono,"
            "showfreqs=s=38x220:r=30:mode=bar:ascale=log:fscale=log:"
            "win_size=4096:overlap=0.80:averaging=4:colors=white,"
            "format=gray16le,scale=38:1:flags=area,"
            "pad=40:1:1:0:color=black,"
            "scale=4266:880:flags=bilinear,crop=4160:880:53:0,"
            "format=gray16le,"
            "geq=lum='st(0\\,min(876\\,940-p(X\\,Y)/64.25));"
            "st(1\\,min(876\\,940-p(107\\,Y)/64.25));"
            "st(2\\,min(876\\,940-p(4052\\,Y)/64.25));"
            "st(3\\,if(lt(X\\,107)\\,"
            "clip(5-abs((ld(1)-876)*X-107*(Y-876))/"
            "sqrt((ld(1)-876)*(ld(1)-876)+11449)\\,0\\,1)\\,"
            "if(gt(X\\,4052)\\,"
            "clip(5-abs((876-ld(2))*(X-4052)-107*(Y-ld(2)))/"
            "sqrt((876-ld(2))*(876-ld(2))+11449)\\,0\\,1)\\,"
            "clip(5-abs(Y-ld(0))\\,0\\,1))));"
            "65535*max("
            "ld(3)\\,"
            "0.55*lte(abs(X-round(X*39/4159)*4159/39)\\,7)*"
            "gt(X\\,7)*lt(X\\,4152)*"
            "gte(Y\\,ld(0))*gt(p(X\\,Y)\\,5140))',"
            "scale=1040:220:flags=lanczos,format=gray,"
            "lut=y='if(lte(val\\,16)\\,0\\,val)',"
            "split=3[linecoremask][lineinnermask][lineoutermask];"
            "[lineinnermask]pad=1168:360:64:70:color=black,"
            "gblur=sigma=5:steps=2,"
            "drawbox=x=0:y=290:w=1168:h=70:color=black:t=fill,"
            "lut=y='if(lte(val\\,16)\\,0\\,val*1.8)'[innerlinemask];"
            "[lineoutermask]pad=1168:360:64:70:color=black,"
            "gblur=sigma=14:steps=2,"
            "drawbox=x=0:y=290:w=1168:h=70:color=black:t=fill,"
            "lut=y='if(lte(val\\,16)\\,0\\,val*1.5)'[outerlinemask];"
            f"color=c=0x{color}:s=1040x220:r=30:d={duration:.3f},format=rgba[linecorecolor];"
            f"color=c=0x{color}:s=1168x360:r=30:d={duration:.3f},format=rgba[lineinnercolor];"
            f"color=c=0x{color}:s=1168x360:r=30:d={duration:.3f},format=rgba[lineoutercolor];"
            "[linecorecolor][linecoremask]alphamerge[linecore];"
            "[lineinnercolor][innerlinemask]alphamerge[lineinner];"
            "[lineoutercolor][outerlinemask]alphamerge[lineouter];"
            "[bgclip][lineouter]overlay=736:226:format=auto[linewide];"
            "[linewide][lineinner]overlay=736:226:format=auto[lineglow];"
            "[lineglow][linecore]overlay=800:296:format=auto[spectrumscene];"
        )
    elif visual_style == "spectrum-mirror":
        spectrum_scene = (
            f"[0:v]format=rgba,trim=start={start:.3f}:end={end:.3f},"
            "setpts=PTS-STARTPTS[bgclip];"
            f"[1:a]atrim=start={start:.3f}:end={end:.3f},"
            "asetpts=PTS-STARTPTS,asplit=2[a][specaudio];"
            "[specaudio]aformat=channel_layouts=mono,"
            "showfreqs=s=38x104:r=30:mode=bar:ascale=log:fscale=log:"
            "win_size=4096:overlap=0.80:averaging=4:colors=white,"
            "format=gray16le,scale=38:1:flags=area,"
            "pad=40:1:1:0:color=black,"
            "scale=4266:416:flags=bilinear,crop=4160:416:53:0,"
            "format=gray16le,"
            "geq=lum='st(0\\,min(412\\,420-p(X\\,Y)/158.0));"
            "st(1\\,min(412\\,420-p(107\\,Y)/158.0));"
            "st(2\\,min(412\\,420-p(4052\\,Y)/158.0));"
            "st(3\\,if(lt(X\\,107)\\,"
            "clip(5-abs((ld(1)-412)*X-107*(Y-412))/"
            "sqrt((ld(1)-412)*(ld(1)-412)+11449)\\,0\\,1)\\,"
            "if(gt(X\\,4052)\\,"
            "clip(5-abs((412-ld(2))*(X-4052)-107*(Y-ld(2)))/"
            "sqrt((412-ld(2))*(412-ld(2))+11449)\\,0\\,1)\\,"
            "clip(5-abs(Y-ld(0))\\,0\\,1))));"
            "65535*max("
            "ld(3)\\,"
            "0.55*lte(abs(X-round(X*39/4159)*4159/39)\\,7)*"
            "gt(X\\,7)*lt(X\\,4152)*gte(Y\\,ld(0))*lte(Y\\,412))',"
            "scale=1040:104:flags=lanczos,format=gray,"
            "lut=y='if(lte(val\\,16)\\,0\\,val)',"
            "pad=1040:220:0:8:color=black,"
            "split=2[mirrorupper][mirrorflipsrc];"
            "[mirrorflipsrc]vflip[mirrorlower];"
            "[mirrorupper][mirrorlower]blend=all_mode=lighten,"
            "split=3[mirrorcoremask][mirrorinnermask][mirroroutermask];"
            "[mirrorinnermask]pad=1168:348:64:64:color=black,"
            "gblur=sigma=5:steps=2,"
            "drawbox=x=0:y=322:w=1168:h=26:color=black:t=fill,"
            "lut=y='if(lte(val\\,16)\\,0\\,val*1.8)'[mirrormaskinner];"
            "[mirroroutermask]pad=1168:348:64:64:color=black,"
            "gblur=sigma=14:steps=2,"
            "drawbox=x=0:y=322:w=1168:h=26:color=black:t=fill,"
            "lut=y='if(lte(val\\,16)\\,0\\,val*1.5)'[mirrormaskouter];"
            f"color=c=0x{color}:s=1040x220:r=30:d={duration:.3f},"
            "format=rgba[mirrorcorecolor];"
            f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
            "format=rgba[mirrorinnercolor];"
            f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
            "format=rgba[mirroroutercolor];"
            "[mirrorcorecolor][mirrorcoremask]alphamerge[mirrorcore];"
            "[mirrorinnercolor][mirrormaskinner]alphamerge[mirrorinner];"
            "[mirroroutercolor][mirrormaskouter]alphamerge[mirrorouter];"
            "[bgclip][mirrorouter]overlay=736:226:format=auto[mirrorwide];"
            "[mirrorwide][mirrorinner]overlay=736:226:format=auto[mirrorglow];"
            "[mirrorglow][mirrorcore]overlay=800:290:format=auto[spectrumscene];"
        )
    elif visual_style == "spectrum-dots":
        spectrum_scene = (
            f"[0:v]format=rgba,trim=start={start:.3f}:end={end:.3f},"
            "setpts=PTS-STARTPTS[bgclip];"
            f"[1:a]atrim=start={start:.3f}:end={end:.3f},"
            "asetpts=PTS-STARTPTS,asplit=2[a][specaudio];"
            "[specaudio]aformat=channel_layouts=mono,"
            "showfreqs=s=52x200:r=30:mode=bar:ascale=log:fscale=log:"
            "win_size=4096:overlap=0.80:averaging=4:colors=white,"
            "scale=1040:200:flags=neighbor,"
            "drawgrid=width=20:height=20:thickness=8:color=black@1,"
            "format=rgba,colorkey=0x000000:0.06:0.08,alphaextract,"
            "pad=1040:220:0:10:color=black,"
            "gblur=sigma=0.65:steps=1,"
            "lut=y='if(lte(val\\,18)\\,0\\,val)',"
            "split=4[dotcoremask][dotinnermask][dotoutermask][dottrailmask];"
            "[dotinnermask]pad=1168:348:64:64:color=black,"
            "gblur=sigma=4:steps=2,lut=y='val*1.8'[dotinner];"
            "[dotoutermask]pad=1168:348:64:64:color=black,"
            "gblur=sigma=14:steps=2,lut=y='val*2.2'[dotouter];"
            "[dottrailmask]pad=1168:348:64:64:color=black,"
            "lagfun=decay=0.93,gblur=sigma=2.2:steps=2,"
            "lut=y='val*0.42'[dottrail];"
            f"color=c=0x{color}:s=1040x220:r=30:d={duration:.3f},"
            "format=rgba[dotcorecolor];"
            f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
            "format=rgba[dotinnercolor];"
            f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
            "format=rgba[dotoutercolor];"
            f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
            "format=rgba[dottrailcolor];"
            "[dotcorecolor][dotcoremask]alphamerge[dotcore];"
            "[dotinnercolor][dotinner]alphamerge[dotinnerglow];"
            "[dotoutercolor][dotouter]alphamerge[dotouterglow];"
            "[dottrailcolor][dottrail]alphamerge[dotafterglow];"
            "[bgclip][dotouterglow]overlay=736:226:format=auto[dotwide];"
            "[dotwide][dotafterglow]overlay=736:226:format=auto[dotheld];"
            "[dotheld][dotinnerglow]overlay=736:226:format=auto[dotglow];"
            "[dotglow][dotcore]overlay=800:290:format=auto[spectrumscene];"
        )
    elif visual_style == "spectrum-waterfall":
        spectrum_scene = (
            f"[0:v]format=rgba,trim=start={start:.3f}:end={end:.3f},"
            "setpts=PTS-STARTPTS[bgclip];"
            f"[1:a]atrim=start={start:.3f}:end={end:.3f},"
            "asetpts=PTS-STARTPTS,asplit=2[a][specaudio];"
            "[specaudio]aformat=channel_layouts=mono,"
            "showspectrum=s=1040x220:slide=scroll:mode=combined:"
            "color=intensity:scale=log:fscale=log:win_func=blackman:"
            "orientation=vertical:overlap=0.85:gain=3:data=magnitude:"
            "fps=30:legend=0:drange=90:limit=0:opacity=1,"
            "format=gray,edgedetect=low=0.035:high=0.11:mode=wires,"
            "gblur=sigma=0.35:steps=1,"
            "lut=y='if(lte(val\\,18)\\,0\\,min(230\\,val*1.5))',"
            "split=3[watercoremask][waterinnermask][wateroutermask];"
            "[waterinnermask]pad=1168:348:64:64:color=black,"
            "gblur=sigma=2.5:steps=2,lut=y='val*1.2'[waterinner];"
            "[wateroutermask]pad=1168:348:64:64:color=black,"
            "gblur=sigma=8:steps=2,lut=y='val*0.8'[waterouter];"
            f"color=c=0x{color}:s=1040x220:r=30:d={duration:.3f},"
            "format=rgba[watercorecolor];"
            f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
            "format=rgba[waterinnercolor];"
            f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
            "format=rgba[wateroutercolor];"
            "[watercorecolor][watercoremask]alphamerge[watercore];"
            "[waterinnercolor][waterinner]alphamerge[waterinnerglow];"
            "[wateroutercolor][waterouter]alphamerge[waterouterglow];"
            "[bgclip][waterouterglow]overlay=736:226:format=auto[waterhalo];"
            "[waterhalo][waterinnerglow]overlay=736:226:format=auto[waterglow];"
            "[waterglow][watercore]overlay=800:290:format=auto[spectrumscene];"
        )
    else:
        spectrum_scene = (
        f"[0:v]format=rgba,trim=start={start:.3f}:end={end:.3f},"
        "setpts=PTS-STARTPTS[bgclip];"
        f"[1:a]atrim=start={start:.3f}:end={end:.3f},"
        "asetpts=PTS-STARTPTS,asplit=2[a][specaudio];"
        "[specaudio]aformat=channel_layouts=mono,"
        "showfreqs=s=80x220:r=30:mode=bar:ascale=log:fscale=log:"
        f"win_size=4096:overlap=0.80:averaging=4:colors=0x{color},"
        "scale=1040:220:flags=neighbor,"
        "drawgrid=width=13:height=220:thickness=5:color=black@1,"
        "format=rgba,colorkey=0x000000:0.06:0.08,alphaextract,"
        "pad=1040:236:0:8:color=black,"
        "erosion=coordinates=90,erosion=coordinates=90,"
        "erosion=coordinates=90,dilation=coordinates=90,"
        "dilation=coordinates=90,dilation=coordinates=90,"
        "gblur=sigma=0.8:steps=1,"
        "split=5[coremask][specinner][specouter][specwide][specpeak];"
        "[specinner]pad=1168:348:64:56:color=black,"
        "gblur=sigma=4:steps=2,lut=y='val*2.0'[innermask];"
        "[specouter]pad=1168:348:64:56:color=black,"
        "gblur=sigma=14:steps=2,lut=y='val*2.4'[outermask];"
        "[specwide]pad=1168:348:64:56:color=black,"
        "gblur=sigma=28:steps=3,lut=y='val*2.8'[widemask];"
        "[specpeak]pad=1168:348:64:56:color=black,lagfun=decay=0.975,"
        "gblur=sigma=2.2:steps=2,lut=y='val*0.55'[peakmask];"
        f"color=c=0x{color}:s=1040x236:r=30:d={duration:.3f},"
        "format=rgba,colorchannelmixer=rr=1:rg=0.18:rb=0.18:"
        "gr=0.18:gg=1:gb=0.18:br=0.18:bg=0.18:bb=1[corecolor];"
        f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
        "format=rgba[innercolor];"
        f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
        "format=rgba[outercolor];"
        f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
        "format=rgba[widecolor];"
        f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
        "format=rgba[peakcolor];"
        "[corecolor][coremask]alphamerge[core];"
        "[innercolor][innermask]alphamerge[innerglow];"
        "[outercolor][outermask]alphamerge[outerglow];"
        "[widecolor][widemask]alphamerge[wideglow];"
        "[peakcolor][peakmask]alphamerge[peakhold];"
        "[bgclip][wideglow]overlay=736:226:format=auto[wide];"
        "[wide][outerglow]overlay=736:226:format=auto[outer];"
        "[outer][peakhold]overlay=736:226:format=auto[held];"
        "[held][innerglow]overlay=736:226:format=auto[inner];"
        "[inner][core]overlay=800:282:format=auto[spectrumbars];"
        f"[spectrumbars]drawbox=x=800:y=516:w=1040:h=3:"
        f"color=0x{color}@0.85:t=fill[spectrumscene];"
        )

    return (
        spectrum_scene
        +
        f"color=c=black@0.0:s=1040x28:r=30:d={duration:.3f},"
        "format=rgba[progressbase];"
        f"color=c=0x{progress}@0.98:s=1040x6:r=30:d={duration:.3f},"
        "format=rgba[progressfill];"
        "[progressbase][progressfill]overlay="
        f"x='-1040+1040*(t+{start:.3f})/{program_duration:.3f}':"
        "y=11:eval=frame:format=auto[progress];"
        "[progress]split=2[progresscore][progressglowsrc];"
        "[progressglowsrc]gblur=sigma=8:steps=2,"
        "colorchannelmixer=aa=2.0[progressglow];"
        f"color=c=0x{progress}:s=40x40:r=30:d={duration:.3f},"
        "format=rgba,"
        "geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':"
        "a='255*lte((X-19.5)*(X-19.5)+(Y-19.5)*(Y-19.5)\\,100)'"
        "[knobsource];"
        "[knobsource]split=2[knobcore][knobglowsrc];"
        "[knobglowsrc]gblur=sigma=7:steps=2,"
        "colorchannelmixer=aa=1.8[knobglow];"
        f"[spectrumscene]drawbox=x=800:y=548:w=1040:h=6:"
        f"color=0x{progress}@0.34:t=fill[track];"
        "[track][progressglow]overlay=800:537:format=auto[trackglow];"
        "[trackglow][progresscore]overlay=800:537:format=auto[progressscene];"
        "[progressscene][knobglow]overlay="
        f"x='800+1040*(t+{start:.3f})/{program_duration:.3f}-20':"
        "y=531:eval=frame:format=auto[knobhalo];"
        "[knobhalo][knobcore]overlay="
        f"x='800+1040*(t+{start:.3f})/{program_duration:.3f}-20':"
        "y=531:eval=frame:format=auto[visual];"
        f"[visual]setpts=PTS+{start:.3f}/TB,{subtitle_filter},"
        "setpts=PTS-STARTPTS[v]"
    )
