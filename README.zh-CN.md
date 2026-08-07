[English](README.md) | 简体中文

# Karaoke AV1 Video Production Skill

这是一个面向Codex的卡拉OK视频制作Skill，同时提供StrangeUtaGame集成，用于制作、审核、渲染、验证和打包带有可编辑时间轴来源与AV1 4:2:0发布检查的视频。

内置工作流使用日语（`ja`），入口为`run_karaoke_japanese_workflow.py`。其他语言工作流需要各自经过验证的适配器。

## 功能

- 检查→预览→编码→验证的制作流程。
- 语义分段、日文注音词边界审核、可编辑SUG一致性、MMS证据、独立ASR复核和视觉适配检查。
- 互斥的旋转黑胶与实时频谱宽屏布局。
- `wide-layout-v5/no-right-panels`：不显示右侧大框，也不显示黑胶后方的小背板；保留专辑卡片、footer、旋转黑胶和底部字幕面板。
- 频谱采用上下辉光均不会被裁切的安全区域。
- 默认输出为1920x1080、30 fps、`yuv420p`、BT.709的AV1视频与AAC-LC 320 kb/s音频。
- MP4为默认输出；只有显式选择且源音频经确认是FLAC或PCM WAV时才生成MKV。
- 完整输出解码为可选诊断；普通验证使用媒体探测、抽样解码、画面检查和输出身份校验。
- 日文注音验证提供`optional`、`required`和`off`三种模式，默认为`optional`。
- 通过`scripts/pitch_shift_audio.py`对完整混音变调，使用Rubber Band R3 Finer并默认保持共振峰。
- 通过JSON提供专辑配置以及歌曲专用的显示、时间和注音决定。
- 共享单曲一键渲染分别生成preflight/final ASS，并要求ASS身份一致。

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

另行安装`ffmpeg`和`ffprobe`。Rubber Band仅在变调时需要；Whisper、MMS和外部MSST属于可选证据链。使用以下命令检查目标环境：

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
4. 制作需要额外时间证据时，使用MMS、独立ASR或MSST派生证据。
5. 构建当前宽屏构图；使用黑胶布局时重新生成当前旋转黑胶资源。
6. 渲染隔离预览、检查代表帧，并编码所选MP4输出。
7. 验证媒体结构和抽样输出，然后最终化、提升或打包已接受文件。

专辑清单配置示例：

```powershell
$env:KARAOKE_ALBUM_MANIFEST = (Resolve-Path .\config\album.json).Path
uv run --no-sync python scripts/karaoke_timing.py --manifest $env:KARAOKE_ALBUM_MANIFEST --allow-partial-manifest
```

## 共享单曲命令

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

频谱使用`--visual-style spectrum`并省略`--vinyl`；可选添加
`--spectrum-color RRGGBB --progress-color RRGGBB`。黑胶的`--vinyl`只是规范身份输入；workflow会在新输出目录中重新生成并校验当前旋转黑胶，再把生成资源传给renderer。频谱不要求、不探测、不生成、不传递也不报告vinyl。

workflow先独立写入`karaoke-preflight.ass`，再在渲染阶段写入最终
`karaoke.ass`；同一SUG/配置下两者SHA-256身份不一致就失败。默认使用完整时长，并且只生成带AAC-LC音频的MP4。`--smoke-duration`、`--lossless-companion`和`--full-decode`都必须显式选择；默认运行不生成MKV，也不执行full decode。

专辑/批量direct renderer当前仍是仅支持vinyl的路径。共享单曲workflow或预览路径的频谱能力不等于专辑renderer支持频谱。

## 参考文档

每份参考文档都有内容对应的英文和中文版本：

| 主题 | English | 中文 |
|---|---|---|
| AV1、FFmpeg、MP4/MKV | [av1-420-commands.md](references/av1-420-commands.md) | [av1-420-commands.zh-CN.md](references/av1-420-commands.zh-CN.md) |
| SUG、独立ASR、变调 | [asr-sug-pitch.md](references/asr-sug-pitch.md) | [asr-sug-pitch.zh-CN.md](references/asr-sug-pitch.zh-CN.md) |
| 宽屏黑胶/频谱 | [wide-visual-templates.md](references/wide-visual-templates.md) | [wide-visual-templates.zh-CN.md](references/wide-visual-templates.zh-CN.md) |
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
| 构图与渲染 | `build_karaoke_wide_artwork.py`、`render_vinyl_karaoke.py`、`render_karaoke_direct_av1_420_album.py`、`render_karaoke_direct_av1_album.py`、`render_karaoke_direct_hevc444_album.py` |
| 日语工作流 | `karaoke_workflow.py`、`run_karaoke_japanese_workflow.py` |
| 媒体与发布 | `inspect_karaoke_media.py`、`transcode_karaoke_av1.py`、`finalize_karaoke_release.py`、`karaoke_release_snapshot.py`、`package_karaoke_numbered_archives.py` |
| 完整混音变调 | `pitch_shift_audio.py` |

递归安装的包文件为`karaoke_common/__init__.py`、`karaoke_common/layout.py`、`karaoke_common/pronunciation.py`、`karaoke_japanese/__init__.py`和`karaoke_japanese/layout.py`。

仓库支持工具为`check_sug_compatibility.py`、`check_karaoke_environment.py`、`install_strangeutagame_integration.py`、`open_editable_project_with_audio_probe.py`以及`pitch_shift_audio.py`的独立镜像。

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
