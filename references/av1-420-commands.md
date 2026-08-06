# AV1 4:2:0 Command Patterns

[简体中文](av1-420-commands.zh-CN.md) | English

Adapt these patterns to the repository and installed FFmpeg build. Quote paths carefully on Windows and test subtitle filter paths with a short preview first.

## Probe Inputs

```powershell
& $ffprobe -v error `
  -show_entries "format=format_name,start_time,duration:stream=index,codec_type,codec_name,pix_fmt,width,height,r_frame_rate,avg_frame_rate,start_time,duration,sample_rate,channels,channel_layout,color_range,color_space,color_transfer,color_primaries" `
  -of json -- $input
```

Inspect available encoders and their actual pixel formats:

```powershell
& $ffmpeg -hide_banner -encoders
& $ffmpeg -hide_banner -h encoder=av1_nvenc
& $ffmpeg -hide_banner -h encoder=libaom-av1
```

An encoder appearing in the list does not prove the current GPU can run it. Perform a short probe encode before selecting the lane.

## Correct Odd Dimensions

4:2:0 output requires even width and height. Pad before rendering subtitles so ASS layout uses the final canvas:

```text
pad=ceil(iw/2)*2:ceil(ih/2)*2,subtitles=...,format=p010le
```

Use `format=yuv420p` for 8-bit or `format=yuv420p10le` for the libaom 10-bit lane. Scaling is acceptable when the target resolution is intentional; do not silently crop source content.

## Default NVENC 8-Bit Compatibility 4:2:0

Use this legacy-compatible default for 1920x1080 30 fps SDR delivery when the local NVIDIA encoder probe succeeds:

```powershell
& $ffmpeg -nostdin -n -i $input `
  -map 0:v:0 -map 0:a:0? -map_metadata -1 -map_chapters -1 `
  -vf "subtitles='$ass':fontsdir='$fonts',format=yuv420p" `
  -s 1920x1080 -r 30 -pix_fmt yuv420p `
  -c:v av1_nvenc -preset p7 -tune hq -rc vbr -cq 44 -b:v 0 `
  -multipass fullres -lookahead 32 -spatial-aq 1 -temporal-aq 1 `
  -aq-strength 8 -g 240 `
  -colorspace bt709 -color_primaries bt709 -color_trc bt709 `
  -c:a aac -profile:a aac_low -b:a 320k -movflags +faststart `
  $temporaryMp4
```

Verify CQ, multipass, lookahead, AQ, GOP, dimensions, frame rate, pixel format, and BT.709 metadata with `ffprobe`. A separate lossless-audio MKV is optional when the selected source is genuinely lossless; the default compatibility MP4 remains AAC-LC 320k. For an explicitly requested 10-bit lane, use `p010le`/`yuv420p10le` and record that profile separately.

## libaom 10-Bit 4:2:0

Use as the CPU fallback or slower quality lane:

```powershell
& $ffmpeg -nostdin -n -i $input `
  -map 0:v:0 -map 0:a:0? -map_metadata -1 -map_chapters -1 `
  -vf "subtitles='$ass':fontsdir='$fonts',format=yuv420p10le" `
  -c:v libaom-av1 -crf 30 -b:v 0 -cpu-used 4 -row-mt 1 `
  -c:a aac -profile:a aac_low -b:a 320k -movflags +faststart `
  $temporaryMp4
```

Do not treat the example CRF as a universal target. Use a representative preview to choose quality and speed.

## Soft ASS In MKV

Encode video without burn-in, then preserve the editable ASS track:

```powershell
& $ffmpeg -nostdin -n -i $input -i $ass `
  -map 0:v:0 -map 0:a:0? -map 1:0 -map_metadata -1 -map_chapters -1 `
  -c:v av1_nvenc -pix_fmt p010le -c:a libopus -c:s ass `
  -metadata:s:s:0 language=chi `
  $temporaryMkv
```

Set the real subtitle language and confirm the target player supports ASS rendering.

## Paired Lossless-Audio Companion

Keep the MP4 as the default delivery. After it passes its preliminary media gate, copy its video stream into MKV and take audio directly from the original lossless FLAC/PCM-WAV source over the exact same timeline:

```powershell
& $ffmpeg -nostdin -n -i $temporaryMp4 -i $losslessSource `
  -filter_complex "[1:a:0]atrim=start=$start:end=$end,asetpts=PTS-STARTPTS[a]" `
  -map 0:v:0 -map "[a]" -c:v copy -c:a flac `
  $temporaryLosslessMkv
```

Do not add `-shortest`, `-ar`, or `-ac`. Reject MP3/AAC and probe the true source codec instead of trusting the extension. Before paired promotion, require matching encoded video-stream hashes and require decoded MKV PCM to match the equivalently trimmed source PCM.

## Timeline Decisions

- Inspect every input `start_time`, stream duration, frame-rate mode, and the final ASS event before encoding.
- Preserve relative media offsets by default. Do not independently reset video and audio timestamps unless both are normalized to one documented baseline.
- For a still image, set an explicit frame rate and approved program duration that covers the audio and valid ASS events.
- Preserve VFR with an explicit passthrough decision, or convert to a declared CFR after checking timing impact.
- Stop when the final ASS event exceeds the approved program timeline; fix the ASS or extend the timeline explicitly.
- For multiple audio tracks, add each `-map` intentionally and validate each retained stream.

## Verify Delivery

```powershell
& $ffprobe -v error -select_streams v:0 `
  -show_entries stream=codec_name,pix_fmt,width,height,r_frame_rate,color_range,color_space,color_transfer,color_primaries,duration `
  -of json -- $output

& $ffprobe -v error -select_streams a `
  -show_entries stream=codec_name,sample_rate,channels,channel_layout,duration `
  -of json -- $output

& $ffprobe -v error -select_streams s `
  -show_entries stream=index,codec_name,duration:stream_tags=language,title `
  -of json -- $output

& $ffprobe -v error -select_streams s -show_packets `
  -show_entries packet=stream_index,pts_time,duration_time,flags `
  -of json -- $output
```

### Optional Decode Diagnostics

Run these only on user request or when probe, mux, transport, or corruption evidence warrants decoding:

```powershell
& $ffmpeg -v error -xerror -i $output -map 0:v:0 -f null -
& $ffmpeg -v error -xerror -i $output -map 0:a -f null -
```

By default, accept only `av1` video with the intended 4:2:0 pixel format, expected streams, duration, and generation hashes. Full-output null decoding is optional and should run only on user request or when probe, mux, transport, or corruption evidence warrants it. If any full or sampled decode is executed, map every intended stream and record each real exit code; an unperformed diagnostic is neither success nor failure. For soft subtitles, require the expected `ass` stream and verify its first and final packet timestamps remain within the approved program timeline.

## Promote The Verified Output

Resolve the temporary, target, and backup paths and verify they stay within the intended output directory. Keep temporary and target files on the same volume.

On Windows, use the filesystem replacement API when replacing an existing target:

```powershell
[System.IO.File]::Replace($temporary, $target, $backup, $true)
```

When the target does not yet exist, use a same-volume move that refuses overwrite. If replacement is unsupported by the filesystem, preserve the old target as a backup and report that the fallback is not guaranteed atomic.

After promotion, rerun ffprobe against `$target`; restore `$backup` if the final path does not match the verified codec, pixel format, streams, and duration.

## Privacy And Dependency Boundary

- Store probe JSON and FFmpeg logs in a private working directory. Redact absolute paths, filenames, device identifiers, and unapproved tags before reporting or sharing logs.
- Use unique temporary output names and verify input, temporary, target, and backup paths are distinct.
- Strip inherited metadata with `-map_metadata -1` and restore only approved language, title, attribution, or required technical metadata.
- Record `ffmpeg -version` and build configuration. Check the licenses and redistribution terms of FFmpeg, libaom, audio encoders, NVENC components, fonts, and bundled binaries for the intended delivery.
- Do not redistribute FFmpeg binaries, fonts, lyrics, ASS sources, or third-party media merely because the rendered video is authorized.
