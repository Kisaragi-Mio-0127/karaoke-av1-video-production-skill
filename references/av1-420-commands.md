# AV1 4:2:0 Command Patterns

[简体中文](av1-420-commands.zh-CN.md) | English

Use the default tested FFmpeg/FFprobe 8.x pair (currently 8.0.1). Treat 9.x as an explicit compatibility migration. Quote Windows paths correctly and verify subtitle filters and NVENC with short probes.

## Input probing and canvas

Use `ffprobe` to inspect the container, start time, duration, video codec, pixel format, dimensions, frame rate, color metadata, sample rate, channel count, and channel layout. Encoder listings and help output describe available parameters but do not prove that the current GPU can run them, so perform a short probe encode first.

4:2:0 requires even dimensions. Pad or scale before subtitle rendering, for example:

```text
pad=ceil(iw/2)*2:ceil(ih/2)*2,subtitles=...,format=p010le
```

Use `yuv420p10le` for 10-bit software encoding, `p010le` for 10-bit NVENC input, and `yuv420p` for 8-bit compatibility output. Do not crop the source implicitly.

## AV1 encoding and the default MP4

After a successful NVENC probe, the default 1920x1080, 30 fps SDR profile is AV1 NVENC CQ38, preset p7, tune hq, VBR, full-resolution multipass, lookahead 32, spatial and temporal AQ, AQ strength 8, GOP 240, `yuv420p`, and BT.709. Use `libaom-av1` for the CPU quality path and record separate 10-bit profiles explicitly.

The default delivery is MP4 with AAC-LC audio at 320 kb/s and explicit stream mapping:

```powershell
& $ffmpeg -nostdin -n -i $input `
  -map 0:v:0 -map 0:a:0? -map_metadata -1 -map_chapters -1 `
  -vf "subtitles='$ass':fontsdir='$fonts',format=yuv420p" `
  -s 1920x1080 -r 30 -pix_fmt yuv420p `
  -c:v av1_nvenc -preset p7 -tune hq -rc vbr -cq 38 -b:v 0 `
  -multipass fullres -rc-lookahead 32 -spatial-aq 1 -temporal-aq 1 `
  -aq-strength 8 -g 240 `
  -colorspace bt709 -color_primaries bt709 -color_trc bt709 `
  -c:a aac -profile:a aac_low -b:a 320k -movflags +faststart `
  $temporaryMp4
```

Verify the codec, profile, dimensions, frame rate, pixel format, BT.709 metadata, audio streams, and duration with `ffprobe`. Verify requested CQ, preset, multipass, lookahead, AQ, and GOP settings from the render report; they are not all recoverable from the final container. Produce an H.264 fallback when the target device has uncertain AV1 support. MKV is a separate explicit selection and accepts only a probed FLAC or PCM WAV source; reject MP3 and AAC sources. Do not create MKV or run a full decode unless explicitly selected.

```powershell
& $ffprobe -v error -show_entries `
  "format=format_name,start_time,duration:stream=index,codec_type,codec_name,profile,pix_fmt,width,height,r_frame_rate,sample_rate,channels,channel_layout" `
  -of json $media
```

## Soft ASS and FLAC-in-MKV

Use MKV for preserved ASS soft subtitles or multiple tracks. After the MP4 passes its initial gates, an explicitly selected lossless companion can copy the verified video stream and encode FLAC directly from the matching interval of a lossless source:

```powershell
& $ffmpeg -nostdin -n -i $temporaryMp4 -i $losslessSource `
  -filter_complex "[1:a:0]atrim=start=$start:end=$end,asetpts=PTS-STARTPTS[a]" `
  -map 0:v:0 -map "[a]" -c:v copy -c:a flac `
  $temporaryLosslessMkv
```

Do not add `-shortest`, `-ar`, or `-ac`. Preserve the lossless source sample rate and channel structure. Before paired release, require MP4 AAC-LC/320k metadata, FLAC-only MKV audio, identical encoded video streams, matching timelines, and decoded MKV PCM equal to the selected source interval.

## Subtitle overlay and supplied footage

The editor overlay output is a silent 1920x1080, 30 fps ProRes 4444 MOV. Render
ASS onto a transparent RGBA source with the subtitle filter's alpha processing
enabled, encode profile 4 with `prores_ks`, and verify a `yuva444p*` pixel
format, the `ap4h`/4444 profile, duration, dimensions, frame rate, and absence
of audio.

When `--background-video` is supplied, scale it to fit within 1920x1080 and pad
the unused area with black. Apply `tpad=stop_mode=add` followed by an explicit
duration trim: this trims long footage and fills a short source with black
through the selected song interval. Burn ASS after that timeline operation,
map only the selected song audio, and use the normal AV1 4:2:0/AAC settings.
Do not use `-shortest`.

## Timeline, verification, and promotion

- Inspect input `start_time`, stream durations, frame-rate mode, and final ASS events; do not use `-shortest` to conceal drift.
- Map and verify every selected stream explicitly.
- Probe the final video codec, pixel format, dimensions, frame rate, color metadata, audio codec, channels, duration, and subtitle streams.
- Treat a complete null decode as an optional diagnostic that is off by default and enabled only by an explicit selection. When it is run, record the mapped streams, tested window, and real exit code.
- Keep temporary output, destination, and backups on the same volume. Resolve target paths before promotion and probe the final path again after replacement.
