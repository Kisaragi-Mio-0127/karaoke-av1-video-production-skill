# 宽屏视觉模板

[English](wide-visual-templates.md) | 简体中文

每次宽屏渲染只能选择一个右侧视觉效果。黑胶唱片和实时频谱必须互斥，使用同一套构图与预览脚本，不为单曲分叉渲染器。

## 选择模板

- 黑胶旋转布局使用`--visual-style vinyl`，预览时另外提供`--vinyl <vinyl-png>`。
- 发光实时频谱布局使用`--visual-style spectrum`，省略`--vinyl`；当前CLI对多余的黑胶参数会忽略。
- 两种效果不能出现在同一输出中，接受的变体使用不同文件名。
- 模板、构图脚本、频谱行为或布局常量变化后，先渲染并检查短预览，再做全量编码。

## 构图与渲染

在StrangeUtaGame根目录构图：

```powershell
uv run --no-sync python scripts/build_karaoke_wide_artwork.py `
  --background <background> --cover <cover> `
  --font-regular <regular-font> --font-bold <bold-font> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style <vinyl-or-spectrum> --output <composition-png>
```

预览时使用相同的`--visual-style`、`--layout wide`和准备交付的后混音源：

```powershell
uv run --no-sync python scripts/karaoke_review_preview.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --composition <composition-png> --fonts-dir <fonts-dir> `
  --font-file <main-font> --output <new-output-mp4> `
  --lossless-output <new-lossless-output-mkv> `
  --ass-output <new-output-ass> --report-output <new-report-json> `
  --start <seconds> --duration <seconds> --layout wide `
  --visual-style <vinyl-or-spectrum> <vinyl-only-arguments>
```

黑胶替换占位参数为`--vinyl <vinyl-png>`；频谱省略它，可加`--spectrum-color RRGGBB --progress-color RRGGBB`。使用时间覆盖时同时传入`--timing-overrides <json>`和`--song-id <id>`。短预览可省略无损输出，正式全量渲染应保留。默认全节目AV1 4:2:0直出档为1920x1080、30fps、yuv420p、BT.709、AV1 NVENC CQ38、preset p7、tune hq、VBR、全分辨率multipass、lookahead32、空间与时间AQ、AQ strength8、GOP240，并且必须先通过硬件探测。默认兼容MP4使用AAC-LC 320k；选定源确实无损时可另配无损音频MKV。不要显示耗时或播放控制按钮。

## 当前宽屏约束

这些是当前1920×1080 StrangeUtaGame模板常量，不是所有项目的通用坐标；画布或渲染器变化后应重新读取脚本。

- 下方字幕背景矩形为`(20,576,1900,1050)`，字幕锚点由`--layout`决定。
- 黑胶专辑卡为`(x,y,width,height)=(40,30,340,402)`，标题块可见左边界为`430`。
- 频谱专辑卡为`(40,30,460,522)`，标题/频谱/进度的可见左边界为`800`；频谱为`(800,290,1040,220)`，基线`y=516`，进度条为`(800,548,1040,6)`，圆形指示器直径约20 px。
- 频谱目标为80个上下圆角柱，保留水平和底部辉光余量、近期峰值保持及圆形进度指示器；在原始分辨率帧中检查边缘、辉光和终点，不只相信常量报告。
- 活动柱和辉光使用审核过的封面主色，进度条优先使用审核过的封面辅色；没有辅色时记录回退色。
- 必须生成并检查报告中的视觉样式、颜色、几何、柱数/圆角、辉光余量、峰值保持、进度几何和`show_time: false`。另行计算构图、后混音、黑胶、ASS、报告和输出的SHA-256，因为脚本不会自动记录全部输入身份。

## 提升前检查

构图/渲染、帧检查、媒体探测、提升、目标重探测和回滚保留是独立门禁。检查片头、低能量段、峰值段、密集歌词和片尾；黑胶额外检查至少四个旋转相位且无接缝或扫过的色块，频谱检查实时响应、峰值衰减、圆角底部、未裁切辉光、标题对齐和进度终点。
