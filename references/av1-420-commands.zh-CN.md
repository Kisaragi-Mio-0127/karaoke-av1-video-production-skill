# AV1 4:2:0命令模式

[English](av1-420-commands.md) | 简体中文

根据仓库和本机FFmpeg版本调整命令。Windows路径要正确引用；字幕滤镜先用短预览验证。

## 输入探测与画布

使用`ffprobe`检查容器、起始时间、时长、视频编码、像素格式、尺寸、帧率、色彩元数据、采样率、声道数和声道布局。用`ffmpeg -encoders`及对应编码器帮助确认可用参数，但编码器出现在列表中不代表本机GPU能够运行；必须先做短探测编码。

4:2:0要求宽高为偶数。应在字幕渲染前明确补边或缩放，例如：

```text
pad=ceil(iw/2)*2:ceil(ih/2)*2,subtitles=...,format=p010le
```

10位软件路径使用`yuv420p10le`，NVENC输入使用`p010le`，8位兼容路径使用`yuv420p`。不要静默裁剪源画面。

## AV1编码与默认兼容MP4

NVENC探测成功时，默认1920x1080、30fps、SDR发布档使用AV1 NVENC CQ38、preset p7、tune hq、VBR、全分辨率multipass、lookahead32、空间与时间AQ、AQ strength8、GOP240、yuv420p和BT.709。CPU质量路径使用`libaom-av1`；10位路径另行记录，不能把示例CQ/CRF当作默认标准。

默认主交付是MP4，音频使用AAC-LC，目标码率为320 kb/s，并显式映射视频和选定音轨：

```powershell
& $ffmpeg -nostdin -n -i $input `
  -map 0:v:0 -map 0:a:0? -map_metadata -1 -map_chapters -1 `
  -vf "subtitles='$ass':fontsdir='$fonts',format=yuv420p" `
  -s 1920x1080 -r 30 -pix_fmt yuv420p `
  -c:v av1_nvenc -preset p7 -tune hq -rc vbr -cq 38 -b:v 0 `
  -multipass fullres -lookahead 32 -spatial-aq 1 -temporal-aq 1 `
  -aq-strength 8 -g 240 `
  -colorspace bt709 -color_primaries bt709 -color_trc bt709 `
  -c:a aac -profile:a aac_low -b:a 320k -movflags +faststart `
  $temporaryMp4
```

用`ffprobe`验证CQ、multipass、lookahead、AQ、GOP、尺寸、帧率、像素格式和BT.709元数据。不确定目标设备是否支持AV1时，另产H.264兼容版本。默认兼容输出只有AAC-LC 320k的MP4，不生成MKV。MKV必须通过`--lossless-companion`或底层显式`--lossless-output`主动选择，并且只接受探测确认为FLAC或PCM WAV的源；MP3/AAC必须拒绝，不得把MP4 AAC转成无损音轨。

## ASS软字幕与FLAC-MKV配对

需要保留ASS软字幕或多音轨时使用MKV；复杂ASS样式不要承诺在MP4中生效。只有显式请求无损伴侣且MP4已通过初步门禁时，若源音频确为无损FLAC或PCM WAV，才从通过验证的MP4复制视频流，并从同一裁剪时间段的无损源直接编码FLAC：

```powershell
& $ffmpeg -nostdin -n -i $temporaryMp4 -i $losslessSource `
  -filter_complex "[1:a:0]atrim=start=$start:end=$end,asetpts=PTS-STARTPTS[a]" `
  -map 0:v:0 -map "[a]" -c:v copy -c:a flac `
  $temporaryLosslessMkv
```

不要添加`-shortest`、`-ar`或`-ac`。未显式请求时不要创建或期待无损输出；真实源编码为MP3/AAC或其他有损格式时拒绝无损配对，即使文件扩展名写成FLAC/WAV。MKV保留无损源的采样率和声道结构，不强行套用MP4的44.1 kHz立体声转换。发布前要求MP4为AAC-LC/320k、MKV仅含FLAC音频、两者视频流哈希一致、MKV解码PCM等于源音频切片，时间轴在容差内一致，并具备可回滚的成对发布记录。

## 时间轴、验证与发布

- 检查所有输入的`start_time`、流时长、帧率模式和最终ASS事件；不要用`-shortest`掩盖漂移。
- 多音轨必须逐条显式`-map`并验证；不要依赖FFmpeg自动选流。
- 用`ffprobe`检查视频编码、像素格式、尺寸、帧率、色彩、音频编码、声道、时长和字幕流。
- 完整空解码始终是可选诊断，不是发布强制门禁，只在用户要求或探测、封装、传输、损坏证据需要时执行。仅缺少完整解码证据不得阻止发布、降低验证状态，也不得要求每个成品都具备解码退出码。执行时记录每个真实退出码；未执行既不是成功也不是失败。
- 临时文件、目标和备份应在同一卷；解析并约束路径后再提升。替换已有Windows目标时优先使用文件系统替换API，发布后再次探测最终路径。
