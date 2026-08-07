[English](README.md) | 简体中文

# Karaoke AV1 Video Production Skill

这是一个面向Codex的卡拉OK视频制作Skill，同时提供StrangeUtaGame集成，用于制作、审核、渲染、验证和打包带有可编辑时间轴来源与AV1 4:2:0发布检查的视频。

内置公共分发提供两个并列的日语（`ja`）单曲入口。`run_karaoke_japanese_workflow.py`是默认入口，永远不运行MMS。`run_karaoke_japanese_mms_workflow.py`是已安装的显式MMS入口；它从已有manifest、规范SUG、冻结歌词和项目本地MSST Vocals开始，执行`audit -> build -> render`。当前公共分发路径经验证仅支持日语（`ja`）；其他语言工作流需要各自经过验证的适配器，且不属于此分发。

## 功能

- 检查→预览→编码→验证的制作流程。
- 语义分段、日文注音词边界审核、可编辑SUG一致性、显式MMS审计/覆盖证据、独立ASR复核和视觉适配检查。
- 通过确定性离线封面提取生成有序8色调色板、封面和提取器身份，并为所有受支持配置构建唯一的`karaoke-color-plan/v1`。
- 按显式SUG的`singer_id`路由多演唱者，先解析字符级→句级→项目默认的有效歌手，只为实际出现的歌手按歌词首字符出现顺序分配颜色槽位；应用明确的颜色优先级，活动的Main、Glow、提示字幕和顶部叠加层使用一致的歌手颜色，未激活文字保持白色。
- 显式的`opera`、`harmony`和`secondary`角色使用顶部居中叠加层，安全带为`y=0..96`、锚点为`y=12`，默认字号为`60 px`，长句最低缩小到`36 px`；实际outline/glow保留区延伸到`y=107`；跨歌手注音必须拒绝。
- 互斥的旋转黑胶与实时频谱宽屏布局；正式渲染默认使用封面颜色来源，`project`仅用于回滚兼容。
- `wide-layout-v7/cover-palette`：不显示右侧大框，也不显示黑胶后方的小背板；保留专辑卡片、footer、旋转黑胶和底部字幕面板。标题label/title/artist位置为`y=120/155/220`；标题区使用实际ink bounds，并与secondary保留区至少保持`16 px`间距。
- 频谱采用上下辉光均不会被裁切的安全区域。
- 默认输出为1920x1080、30 fps、`yuv420p`、BT.709的AV1视频与AAC-LC 320 kb/s音频。
- MP4为默认输出；只有显式选择且源音频经确认是FLAC或PCM WAV时才生成MKV。
- 完整输出解码为可选诊断；普通验证使用媒体探测、抽样解码、画面检查和输出身份校验。
- 注音验证默认是可选的；日文结构性注音门禁仍然必须通过，`required`和`off`是显式模式。
- 通过`scripts/pitch_shift_audio.py`对完整混音变调，使用Rubber Band R3 Finer并默认保持共振峰。
- 通过JSON提供专辑配置以及歌曲专用的显示、时间和注音决定。
- 单曲一键和AV1批量渲染共享同一底层renderer，分别生成preflight/final ASS，并要求颜色计划身份一致。

## 颜色计划

确定性离线封面提取器输出恰好8种有序颜色，并生成`cover_sha256`和当前提取器哈希。renderer只构建一个`karaoke-color-plan/v1`；一键和批量只是同一实现的两个入口。

当前取色器会先排除近黑色相噪声，并按Lab邻域的像素面积聚合候选颜色；稀有JPEG噪声不应被提亮成主色。

解析有效歌手后，按歌词字符首次出现的顺序，为实际出现的`singer_id`依次分配主色、次色和第三色；未出现的歌手不占用槽位。颜色优先级为：
`explicit singer_id=#RRGGBB` > 显式主色或次色槽覆盖 > 封面调色板 > 项目策略SUG颜色。主色同步首位歌手和频谱；次色同步第二歌手，单歌手时使用`palette[1]`，并同步进度条。

构图元数据包含`cover_sha256`、当前提取器哈希和有序`palette`。ASS、视频和工作流输出记录相同的`color_plan_sha256`；颜色计划不会修改源SUG。封面、提取器、调色板或颜色计划元数据过时或不一致时，必须 fail closed。

## 安装

将仓库克隆到Codex技能目录：

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

在Codex中调用：

```text
$karaoke-av1-video-production
```

先预览StrangeUtaGame集成复制计划，再执行安装：

```powershell
$skillRoot = (Resolve-Path .).Path
$projectRoot = (Resolve-Path .\project).Path
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target $projectRoot --dry-run
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target $projectRoot
```

## 环境

复用项目已有的`.venv`。只有环境不存在或依赖文件发生变化时才运行`uv sync`；普通命令使用`uv run --no-sync`：

```powershell
$projectRoot = (Resolve-Path .\project).Path
Set-Location $projectRoot
if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
  uv sync
}
uv run --no-sync python --version
```

另行安装`ffmpeg`和`ffprobe`。Rubber Band仅在变调时需要；Whisper和外部MSST属于可选证据链。默认一键和批量入口永远不会生成、消费或校验MMS。下面描述的显式MMS入口默认离线；独立审计/构建脚本仍可作为单独的证据准备工具。使用以下命令检查目标环境：

```powershell
$skillRoot = (Resolve-Path .).Path
$projectRoot = (Resolve-Path .\project).Path
Set-Location $projectRoot
uv run --no-sync python "$skillRoot/scripts/check_karaoke_environment.py" --target $projectRoot
```

## 工作流

1. 通过显式路径或环境变量提供专辑清单以及显示、时间或注音覆盖JSON。
2. 探测源媒体并选择输出配置。
3. 构建或更新规范SUG，然后审核语义分段和适用的注音范围。
4. 运行确定性离线封面提取器，记录有序8色调色板以及封面和提取器身份，并构建共享颜色计划。
5. 显式选择时间证据路径：默认一键和批量入口永远不运行MMS；已安装的MMS入口要求已有manifest、SUG、冻结歌词和MSST Vocals，并执行`audit -> build -> render`；独立MMS审计/构建脚本、独立ASR和MSST派生证据仍是单独的证据工具。
6. 构建当前宽屏构图；使用黑胶布局时重新生成当前旋转黑胶资源。
7. 渲染隔离预览、检查代表帧，并编码所选MP4输出。
8. 验证媒体结构和抽样输出，然后最终化、提升或打包已接受文件。

专辑清单配置示例：

```powershell
$env:KARAOKE_ALBUM_MANIFEST = (Resolve-Path .\config\album.json).Path
uv run --no-sync python scripts/karaoke_timing.py --manifest $env:KARAOKE_ALBUM_MANIFEST --allow-partial-manifest
```

## 单曲工作流入口

### 默认单曲命令

内置一键入口为`scripts/run_karaoke_japanese_workflow.py`，默认使用
`--visual-style vinyl`；两种视觉风格都必须使用不存在的全新输出目录：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --composition <composition-png> --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style vinyl --vinyl <canonical-vinyl-png>
```

频谱使用`--visual-style spectrum`并省略`--vinyl`。正式渲染默认使用封面颜色来源；`project`仅用于回滚兼容。黑胶的`--vinyl`只是规范身份输入；workflow会在新输出目录中重新生成并校验当前旋转黑胶，再把生成资源传给renderer。频谱不要求、不探测、不生成、不传递也不报告vinyl。

workflow先构建共享的`karaoke-color-plan/v1`，再独立写入`karaoke-preflight.ass`，并在渲染阶段写入最终`karaoke.ass`。ASS、视频和工作流输出记录相同的`color_plan_sha256`；构图颜色记录过时或不一致时必须 fail closed，源SUG保持不变。默认使用完整时长，并且只生成带AAC-LC音频的MP4。MKV和完整解码必须显式选择；默认运行不生成MKV，也不执行完整解码。注音验证默认是可选的；日文结构性注音检查仍然必须通过，`required`和`off`仍需显式选择。一键和批量入口使用同一renderer和门禁。仅在需要显式覆盖某个歌手颜色时，才在任一入口重复传入`--singer-color <singer-id>=#RRGGBB`；该设置优先于颜色槽和封面调色板。

默认入口没有MMS参数，永远不会生成、消费或校验MMS。`audit_karaoke_mms_alignment.py`和`build_karaoke_mms_overrides.py`仍是显式独立脚本；只有单独请求证据路径，或使用下面的显式入口时，才使用它们。

### 显式MMS单曲工作流

`scripts/run_karaoke_japanese_mms_workflow.py`是公共集成中已安装的显式MMS入口。

运行前，所选项目配置必须已经能够解析以下输入：

- manifest及其选定的源音频；
- 已审核的规范SUG；
- 审计使用的冻结歌词；
- 带有自身来源记录的项目本地MSST Vocals。

每次运行都必须写入全新的、非deliverables输出根目录。不得直接写入deliverables目录，也不得复用之前的输出根目录。wrapper只在其中创建`audit/`、`build/`和`render/`三个子目录；`render/`是成片工作目录。阶段顺序固定且不可跳过：

```text
audit -> build -> render
```

`audit`使用已有SUG、冻结歌词、源音频和MSST Vocals运行MMS。审计门禁在任何必需输入或身份缺失、过时、不匹配、未解决或被否决时fail closed。`build`只能从通过的审计开始，并生成`build/timing_overrides.json`。构建门禁必须继续携带manifest、SUG、冻结歌词、MSST Vocals、MMS访问策略和审计身份。

在MMS构建产物中，只有`visual_release_overrides_ms`字段会复制到渲染输入并允许影响ASS/视频；它是构建产物中的概念字段，不是目录名。`character_overrides_ms`只保留为审计/构建证据和来源记录；本MMS工作流不会把它应用到SUG、ASS时间或编码视频。渲染门禁要求构建门禁通过且来源记录匹配，然后在输出报告中记录audit/build/render身份。默认入口不得跳过或静默替代其中任何阶段。

MMS模型访问默认离线。可选的`--mms-model-path <local-mms-model>`覆盖优先级最高；未提供时，wrapper先自动发现项目`.cache/torch/hub/checkpoints/model.pt`，再查找该目录中的其他本地`.pt`检查点。只有本地检查点不存在且未授予`--allow-mms-network`时，才会在推理前失败。封面提取有独立策略：只有传入`--allow-cover-network`才允许联网；两项权限互不授权，wrapper不接受通用模型路径或通用网络权限别名。

已安装wrapper的CLI为：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <existing-manifest> --song-id <song-id> `
  --composition <composition.png> `
  --output-dir <new-non-existent-mms-output-dir> `
  --visual-style spectrum
```

manifest负责解析选定歌曲的规范SUG和源音频；默认从所选manifest deliverable的`sources/netease_lyrics.json`解析冻结歌词，并从项目`.cache/msst-vocals`树解析MSST Vocals。`--source <frozen-lyrics>`和`--vocals-root <msst-vocals-root>`只用于显式覆盖，模型路径覆盖同样可选。唯一的网络权限是`--allow-mms-network`和相互独立的`--allow-cover-network`。

## AV1 4:2:0 批量命令

AV1 4:2:0 批量入口为
`scripts/render_karaoke_direct_av1_420_album.py`，支持
`--visual-style vinyl|spectrum|both`，默认使用`vinyl`：

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <album-manifest> `
  --visual-style <vinyl|spectrum|both>
```

该批量入口与一键命令使用同一个renderer和`karaoke-color-plan/v1`构建器，不是第二套工作流。`spectrum`不要求、不探测、不生成、不传递也不报告vinyl资源。
`both`会分别生成vinyl和spectrum两个独立的AV1 4:2:0成品，并为每个变体保留独立的媒体与报告身份。同一song/profile的两种风格复用同一个颜色计划和profile ASS，并按顺序发布；它不是把两种效果合成到同一个文件。
`--single-track`表示只选择一个song和一个profile，因此
`--single-track --visual-style both`可以为同一个song/profile生成两个视觉版本：

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <album-manifest> --song <song-id> --profile wide `
  --single-track --visual-style both
```

`--lossless-companion`和`--full-decode`仍然是对所选风格显式开启的opt-in；
`both`不会隐式开启任一选项。每个独立成品都必须执行
[批量发布门禁](references/batch-release-gates.zh-CN.md)中的发布与回滚检查。

正式AV1 4:2:0批量渲染不会运行MMS。如果固定路径
`<album-root>/sources/timing_overrides.json`存在，批量renderer会自动消费其中已有的visual-release覆盖（并记录文件身份）；这不是MMS运行、审计或参数，批量renderer也不会创建该文件。

## 参考文档

每份参考文档都有内容对应的英文和中文版本：

| 主题 | English | 中文 |
|---|---|---|
| AV1、FFmpeg、MP4/MKV | [av1-420-commands.md](references/av1-420-commands.md) | [av1-420-commands.zh-CN.md](references/av1-420-commands.zh-CN.md) |
| SUG、独立ASR、变调 | [asr-sug-pitch.md](references/asr-sug-pitch.md) | [asr-sug-pitch.zh-CN.md](references/asr-sug-pitch.zh-CN.md) |
| 宽屏黑胶/频谱 | [wide-visual-templates.md](references/wide-visual-templates.md) | [wide-visual-templates.zh-CN.md](references/wide-visual-templates.zh-CN.md) |
| 歌手身份与副唱叠加 | [singer-overlays.md](references/singer-overlays.md) | [singer-overlays.zh-CN.md](references/singer-overlays.zh-CN.md) |
| 字幕时间轴与质量 | [subtitle-timing-quality.md](references/subtitle-timing-quality.md) | [subtitle-timing-quality.zh-CN.md](references/subtitle-timing-quality.zh-CN.md) |
| 批量发布 | [batch-release-gates.md](references/batch-release-gates.md) | [batch-release-gates.zh-CN.md](references/batch-release-gates.zh-CN.md) |
| StrangeUtaGame集成 | [strangeutagame-integration.md](references/strangeutagame-integration.md) | [strangeutagame-integration.zh-CN.md](references/strangeutagame-integration.zh-CN.md) |

## 集成文件映射

实际安装文件以依赖清单为准。

| 阶段 | 入口或模块 |
|---|---|
| 配置与文本 | `karaoke_album.py`、`karaoke_language.py` |
| 时间轴与可编辑SUG | `karaoke_timing.py`、`karaoke_review_preview.py`、`sync_karaoke_editable_ruby.py`、`sug_ruby.py` |
| 对齐证据 | `audit_karaoke_asr_recognition.py`、`audit_karaoke_mms_alignment.py`、`build_karaoke_mms_overrides.py`、`prepare_karaoke_msst_vocals.py` |
| 构图与渲染 | `karaoke_cover_palette.py`、`karaoke_color_plan.py`、`build_karaoke_wide_artwork.py`、`render_vinyl_karaoke.py`、`karaoke_direct_album_planning.py`、`render_karaoke_direct_av1_420_album.py`、`render_karaoke_direct_hevc444_album.py` |
| 日语工作流 | `karaoke_workflow.py`、`run_karaoke_japanese_workflow.py`、`run_karaoke_japanese_mms_workflow.py` |
| 媒体与发布 | `inspect_karaoke_media.py`、`transcode_karaoke_av1.py`、`finalize_karaoke_release.py`、`karaoke_release_snapshot.py`、`package_karaoke_numbered_archives.py` |
| 完整混音变调 | `pitch_shift_audio.py` |

递归安装的包文件为`karaoke_common/__init__.py`、`karaoke_common/layout.py`、`karaoke_common/pronunciation.py`、`karaoke_japanese/__init__.py`和`karaoke_japanese/layout.py`。

仓库支持工具为`check_sug_compatibility.py`、`check_karaoke_environment.py`、`install_strangeutagame_integration.py`、`open_editable_project_with_audio_probe.py`以及`pitch_shift_audio.py`的独立镜像。

专辑直出时，AV1 4:2:0使用`render_karaoke_direct_av1_420_album.py`，HEVC 4:4:4使用`render_karaoke_direct_hevc444_album.py`。清单选择和任务规划等共享流程位于`karaoke_direct_album_planning.py`。

## 仓库结构与测试

```text
.
├── SKILL.md
├── LICENSE
├── NOTICE.md
├── THIRD_PARTY_NOTICES.md
├── agents/
├── examples/
├── integration/strangeutagame/
├── references/
├── scripts/
└── tests/
```

```powershell
uv run --no-sync python -m unittest discover -s scripts -p "test_*.py" -v
uv run --no-sync python -m unittest discover -s tests -p "test_*.py" -v
```

## 许可证

仓库代码和文档使用GPL-3.0-only。详见[NOTICE.md](NOTICE.md)和[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
