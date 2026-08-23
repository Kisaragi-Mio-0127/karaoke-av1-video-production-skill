# 宽屏视觉模板

[English](wide-visual-templates.md) | 简体中文

当前宽屏布局几何只有一个真源。`karaoke-av1-video-production`和`chinese-english-karaoke-production`都链接到这里；不要将这些常数复制到`SKILL.md`、其他命令参考或语言适配器中。

在更改构图、字幕位置、次级叠加层、黑胶唱片图稿或频谱渲染前阅读本文件。渲染器或画布变化时重新核对实现。

## 模板选择

- 只选择一个`--visual-style`：`vinyl`、`spectrum`或`spectrum-line`。
- `vinyl`保持唱片旋转，并在当前运行的输出目录内生成唱片资源。
- 两种频谱样式均省略`--vinyl`，且不得探测、生成、传递或报告vinyl资源。
- 绝不要在一个输出中合并两种效果。AV1批量入口的`both`选项会创建两个独立输出，而不是合并帧。
- `--output-mode subtitle-overlay`属于输出模式覆盖项，独立于两种美术样式。编码画面省略构图、封面、标题、面板、黑胶和频谱，同时保留相同的宽屏ASS几何；提供`--background-video`后，视频素材成为画面源。

## 当前构图

- 构图标识：`wide-layout-v7/cover-palette`。
- 画布：`1920x1080`。
- 专辑卡片、卡片页脚和下方字幕面板保持可见。vinyl显示旋转唱片；spectrum用频谱和进度显示替代该区域。外侧右面板和紧凑的深色vinyl背板保持不显示。
- 必需的无面板报告字段：`right_panel_visible=false`、`outer_right_panel_visible=false`、`vinyl_backplate_present=false`和`vinyl_backplate_preserved=false`。

## 图稿几何

- 下方字幕面板：`(x1,y1,x2,y2)=(20,576,1900,1050)`。
- Vinyl专辑卡片：`(x,y,width,height)=(40,30,340,402)`。
- Spectrum专辑卡片：`(x,y,width,height)=(40,30,460,522)`。
- Vinyl标题视觉左边缘：`430`。
- Spectrum标题、频谱和进度视觉左边缘：`800`。
- Vinyl页脚底部内边距：`12`。
- 标题标签/标题/艺术家基线：`y=120/155/220`，按实际墨迹边界定位。

## 字幕与次级几何

- 宽屏上方轨道：主文本`y=660`，注音锚点`y=625`。
- 宽屏下方轨道：主文本`y=870`，注音锚点`y=835`。
- 主轨道间距：`210 px`；注音到主文本锚点间距：`35 px`。
- 日文/中文宽屏主字号目标：`108 px`；共用注音字号目标：`51 px`；提示字幕文字目标字号：`39 px`。
- 宽屏发布校验拒绝依靠静默缩小主歌词字号来容纳长句。日文长句必须按`subtitle-timing-quality.zh-CN.md`中的分行流程处理，并保持`108 px`目标字号。
- 英文宽屏主字号目标：`96 px`；例外最小字号：`54 px`。
- 英文单词串使用`0 em`的词内附加字距、`0.85`的`Pillow-to-libass`前进位置系数，以及`0.18 em`的总词间距目标。
- 次级角色（`opera`、`harmony`、`secondary`）使用居中的顶部叠加层：内容安全带`y=0..96`，锚点`y=12`，默认字号`60 px`，长行最小字号`36 px`，`outline/glow`保留区延伸至`y=107`。
- 标题墨迹与次级保留区之间至少保持`16 px`。
- 次级内容独立于主轨道、提示字幕和注音。拒绝解析后字符包含多名歌手的注音span。

## 频谱几何

- 频谱绘制矩形：`(x,y,width,height)=(800,290,1040,220)`。
- 折线频谱绘制矩形：`(x,y,width,height)=(800,296,1040,220)`。
- 频谱零幅值坐标：`y=516`。`spectrum-line`不绘制水平基线。
- `spectrum-line`固定包含40个频谱点，并在左右两端各增加一个固定`Y=0`的边界锚点，折线总计42个顶点。相邻点使用直线段连接；每个可见频谱点在相同`x`位置使用2像素宽、55%不透明度的竖线连接到归一化幅值`Y=0`。零值频谱点保持隐藏，避免形成底部横线。高度通道在插值期间保持16位；折线和竖线以`4160x880`绘制（4倍SSAA），再用Lanczos缩小到`1040x220`，得到半径1.25像素的抗锯齿线条。竖线颜色和辉光均延伸到`Y=0`，辉光在该坐标下方裁掉，避免进入进度条区域。两条高斜率边界段使用像素到线段的垂直距离计算覆盖率，确保任意斜率下保持连续。
- 裁剪安全矩形：`(x,y,width,height)=(736,226,1168,348)`。
- 水平辉光内边距：`64 px`。
- 顶部和底部辉光内边距：各`56 px`。
- 顶部和底部条形间隙：各`8 px`。
- 条形数量：`80`；圆角半径：`3 px`；软边sigma：`0.8`。
- 峰值保持：已启用；衰减：`0.975`；半衰期：`0.91 s`。
- 进度轨道：`(x,y,width,height)=(800,548,1040,6)`，带圆形`20 px`指示器；`show_time=false`。
- 在原始分辨率帧中验证条形、辉光、标题对齐、峰值响应和进度终点，不要只验证JSON元数据。

## 自动布局与底层检查

一键包装器在新输出目录内生成当前构图。它使用所选封面，在未显式提供背景时派生背景，并仅在`vinyl`模式下创建唱片资源。显式`--composition`是高级覆盖，仍必须通过当前布局标识和几何门禁；不得静默重新引入旧样式。

仅在直接检查图稿构建器或渲染器时使用以下底层命令。

使用真实项目CLI构建构图：

```powershell
uv run --no-sync python scripts/build_karaoke_wide_artwork.py `
  --background <background> --cover <cover> `
  --font-regular <regular-font> --font-bold <bold-font> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style <vinyl-or-spectrum-or-spectrum-line> --output <composition-png>
```

使用匹配样式渲染代表性预览：

```powershell
uv run --no-sync python scripts/render_karaoke_track.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --composition <composition-png> --fonts-dir <fonts-dir> `
  --font-file <main-font> --output <new-output-mp4> `
  --ass-output <new-output-ass> --report-output <new-report-json> `
  --start <seconds> --duration <seconds> --layout wide `
  --visual-style <vinyl-or-spectrum-or-spectrum-line>
```

对于低层`vinyl`渲染器检查，加入同一图稿运行生成的vinyl资源并记录其身份。对于`spectrum`，省略它。每次审核运行都使用新的输出、ASS和报告路径；将已接受产物与回滚副本分开保留。

## 验收证据

要求构图/报告布局标识与本契约匹配。检查标题、第一行歌词、最长行、密集时间、次级叠加层、活动频谱、进度和结尾帧。对于vinyl至少检查四个旋转阶段，拒绝接缝或扫过式残缺弧段。对于spectrum验证实时响应、峰值衰减、圆角条、未裁剪辉光、对齐的标题和进度边界，以及安全终点行为。
