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

先用构图脚本生成静态背景/构图层；黑胶本身继续旋转，并且是独立的当前派生资产。每次正式或测试流程都必须用当前renderer重新生成黑胶，再使用相同的`--visual-style`、`--layout wide`和准备交付的后混音源预览：

```powershell
uv run --no-sync python scripts/karaoke_review_preview.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --composition <composition-png> --fonts-dir <fonts-dir> `
  --font-file <main-font> --output <new-output-mp4> `
  --ass-output <new-output-ass> --report-output <new-report-json> `
  --start <seconds> --duration <seconds> --layout wide `
  --visual-style <vinyl-or-spectrum> <vinyl-only-arguments>
```

黑胶替换占位参数为新生成的`--vinyl <current-vinyl-png>`，记录`vinyl_sha256`并把准确路径显式传给renderer；禁止静默复用规范或旧`vinyl.png`。频谱省略它，可加`--spectrum-color RRGGBB --progress-color RRGGBB`。使用时间覆盖时同时传入`--timing-overrides <json>`和`--song-id <id>`。默认全节目AV1 4:2:0直出档为1920x1080、30fps、yuv420p、BT.709、AV1 NVENC CQ38、preset p7、tune hq、VBR、全分辨率multipass、lookahead32、空间与时间AQ、AQ strength8、GOP240，并且必须先通过硬件探测。默认兼容MP4使用AAC-LC 320k，普通测试/重渲染只产MP4。MKV严格opt-in：只有探测源为FLAC或PCM WAV且显式传入`--lossless-output <new-lossless-output-mkv>`（或workflow的`--lossless-companion`）时才生成；MP3/AAC请求必须拒绝。不要显示耗时或播放控制按钮。

## 当前v3构图契约

当前宽屏构图采用`wide-layout-v5/no-right-panels`（当前生成器报告为
`wide-layout-v5/no-right-panels`）。这里同时删除了宽屏构图额外绘制的
outer right panel（大框）和旋转黑胶后方/下方的compact暗色backplate（小框）。
黑胶仍然旋转，专辑卡片、卡片footer和底部字幕面板保留；频谱版本也不得
重新引入原黑胶区域背景框。

## 当前宽屏约束

这些是当前1920×1080 StrangeUtaGame模板常量，不是所有项目的通用坐标；画布或渲染器变化后应重新读取脚本。

- 下方字幕背景矩形为`(20,576,1900,1050)`，其顶部为`y=576`且继续保留；字幕锚点由`--layout`决定。
- 黑胶专辑卡为`(x,y,width,height)=(40,30,340,402)`，footer底部留白为`12`，标题块可见左边界为`430`。
- 日文宽屏上下主字幕锚点分别为`y=660`和`y=870`。
- 构图报告中的`right_panel: null`/`right_panel_visible: false`以及
  `outer_right_panel: null`/`outer_right_panel_visible: false`确认没有额外叠加的
  outer right panel（大框）；`vinyl_backplate: null`、
  `vinyl_backplate_present: false`和兼容字段
  `vinyl_backplate_preserved: false`确认compact backplate（小框）也不存在。
- 频谱专辑卡为`(40,30,460,522)`，标题/频谱/进度的可见左边界为`800`；频谱为`(800,290,1040,220)`，基线`y=516`，进度条为`(800,548,1040,6)`，圆形指示器直径约20 px。频谱的clip-safe区域为`(736,226,1168,348)`，水平辉光余量为64 px，上下辉光余量各56 px，柱体上下安全余量各8 px。
- 频谱目标为80个上下圆角柱，保留水平、上方和下方辉光余量、柱体上下安全余量、近期峰值保持及圆形进度指示器；在原始分辨率帧中检查上沿峰值、下沿辉光、圆角和终点，不只相信常量报告。
- 活动柱和辉光使用审核过的封面主色，进度条优先使用审核过的封面辅色；没有辅色时记录回退色。
- 必须生成并检查报告中的视觉样式、颜色、几何、柱数/圆角、辉光余量、峰值保持、进度几何和`show_time: false`。另行计算构图、后混音、黑胶、ASS、报告和输出的SHA-256，因为脚本不会自动记录全部输入身份。

## 提升前检查

构图/渲染、帧检查、媒体探测、提升、目标重探测和回滚保留是独立门禁。检查片头、低能量段、峰值段、密集歌词和片尾；黑胶额外检查至少四个旋转相位且无接缝或扫过的色块，频谱检查实时响应、峰值衰减、圆角底部、未裁切辉光、标题对齐和进度终点。
