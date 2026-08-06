[English](README.md) | 简体中文

# Karaoke AV1 Video Production Skill

这是一个面向 Codex 的卡拉 OK 视频制作 Skill，同时提供经过脱敏的
StrangeUtaGame 集成，用于制作、审核、渲染、验证和打包带有可编辑时间轴
来源与 AV1 4:2:0 发布检查的视频。本文件是仓库文档的中文翻译，不改变
公开流程的语言范围。

通用 profile 为兼容旧清单保留日语（`ja`）。公开集成使用
`run_karaoke_japanese_workflow.py`；其他语言必须通过单独验证的 adapter
接入，禁止静默回退到其他 profile 或工作流。

请先阅读 [SKILL.md](SKILL.md)。英文与中文文件是文档翻译对，不代表额外的
歌词生产包装。

## 包含内容

- `SKILL.md` 中的检查 → 预览 → 编码 → 验证流程。
- 语义分段、日文注音词边界、可编辑 SUG 一致性、MMS 与独立 ASR 证据，
  以及歌词视觉适配门禁。
- 宽屏 `vinyl` 与 `spectrum` 模板；每次渲染只能选择一个。
- 当前宽屏构图采用 `wide-layout-v5/no-right-panels`：旋转黑胶卡位于
  `(40,30,340,402)`，footer 底部留白为 `12`，底部字幕面板从 `y=576`
  开始。黑胶后方的 compact 暗色背板与此前移除的大外框都不存在；黑胶、
  专辑卡片、卡片 footer 和底部字幕面板保留。报告中的
  `right_panel_visible=false`、`outer_right_panel_visible=false`、
  `vinyl_backplate_present=false` 与
  `vinyl_backplate_preserved=false` 应与此一致。
- 频谱版本不重新引入原黑胶区域背景框，使用 clip-safe 区域
  `(736,226,1168,348)`、64 px 水平辉光余量、上下各 56 px 辉光余量和
  上下各 8 px 柱体安全余量；必须检查上沿峰值与下沿辉光没有被裁切。
- 默认发布视频为 1920x1080、30 fps、`yuv420p`、BT.709：AV1 NVENC
  CQ38、固定 preset `p7`、`tune hq`、VBR、全分辨率 multipass、
  lookahead 32、空间/时间 AQ、strength 8、GOP 240。默认兼容音频为
  MP4 中的 AAC-LC 320 kb/s。
- MKV 不是隐式伴侣：只有用户明确请求
  `--lossless-companion`（或底层显式 `--lossless-output`），且探测源确为
  FLAC 或 PCM WAV 时才生成；未请求时不创建、不期待也不报告 MKV，
  MP3/AAC 及其他有损源必须拒绝。
- 通过 `scripts/pitch_shift_audio.py` 处理完整混音，默认使用带共振峰保持
  的 Rubber Band R3 Finer；正式流程拒绝把有损音频重新标记为 FLAC。
- 日文注音校验模式为 `optional`、`required` 和 `off`，默认是
  `optional`。在 `optional` 下，缺少 pronunciation sidecar 只记录为未执行，
  不得默认阻塞；结构性注音检查以及 SUG、ASS、最终帧的一致性仍是必需门禁。
- 每次正式或测试流程都重新生成当前旋转黑胶，记录生成器、样式
  `direction-neutral-concentric-grooves/v3/backplate-absent` 与 `vinyl_sha256`，并把准确的新
  路径显式传给渲染器；规范/旧 `vinyl.png` 只能作为身份参照，禁止静默复用。
- 需要的版本为 StrangeUtaGame 1.4.5 与 SUG 存储格式 0.3.0；实际安装的
  文件集合以 `integration/strangeutagame/dependency-manifest.json` 为准。

仓库不包含录音、歌词、专辑信息、字体、封面、模型、凭据、渲染媒体或真实
项目报告。

## 安装 Skill 与集成

将公开仓库克隆到 Codex 技能目录：

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

在 Codex 中调用：

```text
$karaoke-av1-video-production
```

集成依赖已获授权的 StrangeUtaGame 工作树。先预览复制计划，再安装：

```powershell
$skillRoot = (Resolve-Path .).Path
$projectRoot = (Resolve-Path .\private-project).Path
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target $projectRoot --dry-run
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target $projectRoot
```

默认复用完整的项目本地 `.venv`。只有环境缺失或依赖文件发生变化时才创建/刷新环境；普通命令使用 `uv run --no-sync`，不需要新建 `UV_CACHE_DIR`：

```powershell
$projectRoot = (Resolve-Path .\private-project).Path
Set-Location $projectRoot
if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
  uv sync
}
# 如果 pyproject.toml、uv.lock 或依赖锁发生变化，再运行：uv sync
uv run --no-sync python --version
```

如果机器尚未安装 `uv`，只需一次性安装；不要每次任务重建环境，也不要为普通运行设置独立的 `UV_CACHE_DIR`。使用任务专属的临时目录/缓存，保留必要报告和产物后清理这些目录/缓存。

另行安装 `ffmpeg`/`ffprobe` 并提供合法可用的 CJK 字体。运行环境检查：

```powershell
$skillRoot = (Resolve-Path .).Path
$projectRoot = (Resolve-Path .\private-project).Path
Set-Location $projectRoot
uv run --no-sync python "$skillRoot/scripts/check_karaoke_environment.py" --target $projectRoot
```

官方链接、脚本边界、私有清单和网络限制见
[StrangeUtaGame 集成说明](references/strangeutagame-integration.zh-CN.md)。

## 制作规则

1. 为录音、歌词、同步/展示、字体、封面、模型和最终分发建立权利清单；
   权利缺失或不确定时停止公开交付。
2. 探测每个输入，并在编码前确定输出矩阵。所有强制门禁通过前保留源媒体，
   写入临时输出。
3. 使用已配置的语言 profile；公开工作流使用日语入口
   `run_karaoke_japanese_workflow.py`。非默认 profile 必须有经过验证的
   adapter。独立 ASR 是独立证据链，不是强制对齐失败后的静默后备；不可用
   或失败时记录为 `unresolved`。
4. 请求升降调时，在时间轴和渲染前处理完整混音。验证后的音频用于时间证据、
   预览和默认 MP4。只有用户明确请求无损伴侣且源为 FLAC/PCM WAV 时才生成
   MKV；禁止从 MP4 AAC 反向制作无损音轨。
5. 使用默认 AV1 发布档，并以 `ffprobe` 验证编码器、像素格式、尺寸、帧率、
   色彩元数据、音频和时长。保留 MP4 作为默认兼容交付。
6. 默认只验证 MP4 这一代。显式请求 MKV 时，再把 MP4 与 MKV 作为同一代
   验证：视频流哈希一致、FLAC 音频来自同一无损源切片、时间轴一致，且解码
   PCM 与源切片一致。
7. 候选注音只填补缺失项并写入规范 SUG，保护已有人工或 legacy 注音。Agent
   按整句歌词、语法、词形、词边界和上下文审核；渲染器只读审核后的 SUG，
   不得在渲染阶段推断或覆盖注音。`optional` 是默认的非阻塞语义 sidecar
   校验，`required` 仅在用户明确要求时使用。
8. 保持源文本、适用注音和上下文读音从可编辑 SUG 到 ASS 与渲染输出的可追溯性。
9. 完整输出 null decode 只在用户明确要求，或 probe、mux、传输/损坏证据
   使其成为必要诊断时执行；它不是默认流程或发布门禁。未执行时记录
   `performed: false`，不能伪造解码退出码。

## 参考文档

每份英文参考都有对应的中文文档版本，并且相互链接：

| 主题 | English | 中文 |
|---|---|---|
| AV1、FFmpeg、MP4/MKV | [av1-420-commands.md](references/av1-420-commands.md) | [av1-420-commands.zh-CN.md](references/av1-420-commands.zh-CN.md) |
| SUG、独立 ASR、变调 | [asr-sug-pitch.md](references/asr-sug-pitch.md) | [asr-sug-pitch.zh-CN.md](references/asr-sug-pitch.zh-CN.md) |
| 宽屏黑胶/频谱 | [wide-visual-templates.md](references/wide-visual-templates.md) | [wide-visual-templates.zh-CN.md](references/wide-visual-templates.zh-CN.md) |
| 字幕时间轴与质量 | [subtitle-timing-quality.md](references/subtitle-timing-quality.md) | [subtitle-timing-quality.zh-CN.md](references/subtitle-timing-quality.zh-CN.md) |
| 批量发布 | [batch-release-gates.md](references/batch-release-gates.md) | [batch-release-gates.zh-CN.md](references/batch-release-gates.zh-CN.md) |
| StrangeUtaGame 集成 | [strangeutagame-integration.md](references/strangeutagame-integration.md) | [strangeutagame-integration.zh-CN.md](references/strangeutagame-integration.zh-CN.md) |

## 私有项目数据

将 `examples/album.example.json` 复制到私有项目目录，替换所有占位信息并显式传入：

```powershell
$env:KARAOKE_ALBUM_MANIFEST = (Resolve-Path .\private\album.json).Path
uv run --no-sync python scripts/karaoke_timing.py --manifest $env:KARAOKE_ALBUM_MANIFEST --allow-partial-manifest
```

歌曲专用的显示、注音和上下文读音决定保存在私有 JSON 中。网络访问默认关闭；
刷新歌词源和获取公开封面都必须显式授权。不要把真实清单、哈希、歌词、字体、
报告或媒体提交到本仓库。

## 脚本边界

公开文档只列出日语/通用入口；安装器实际复制的文件由
`dependency-manifest.json` 严格决定。

| 阶段 | 入口或工具 |
|---|---|
| 清单与文本 | `karaoke_album.py`、`karaoke_language.py` |
| 时间轴与可编辑 SUG | `karaoke_timing.py`、`karaoke_review_preview.py`、`sync_karaoke_editable_ruby.py`、`sug_ruby.py` |
| 对齐证据 | `audit_karaoke_asr_recognition.py`、`audit_karaoke_mms_alignment.py`、`build_karaoke_mms_overrides.py`、`prepare_karaoke_msst_vocals.py` |
| 构图与渲染 | `build_karaoke_wide_artwork.py`、`render_vinyl_karaoke.py`、`render_karaoke_direct_av1_420_album.py`、`render_karaoke_direct_av1_album.py`、`render_karaoke_direct_hevc444_album.py` |
| 日语工作流 | `karaoke_workflow.py`、`run_karaoke_japanese_workflow.py` |
| 媒体与发布 | `inspect_karaoke_media.py`、`transcode_karaoke_av1.py`、`finalize_karaoke_release.py`、`karaoke_release_snapshot.py`、`package_karaoke_numbered_archives.py` |
| 支持工具 | `check_sug_compatibility.py`、`check_karaoke_environment.py`、`pitch_shift_audio.py` |

递归安装的日语/通用包文件为：`karaoke_common/__init__.py`、
`karaoke_common/layout.py`、`karaoke_common/pronunciation.py`、
`karaoke_japanese/__init__.py` 与 `karaoke_japanese/layout.py`。

## 仓库结构与测试

```text
.
├── SKILL.md
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

这些测试验证打包与安全边界，不保证在没有私有素材时完成整首媒体渲染。正式制作
前运行环境检查、命令帮助 smoke test、授权短预览和发布门禁。

## 许可证与权利

仓库附带的 `LICENSE` 明确代码和文档使用 GPL-3.0-only。详见
[NOTICE.md](NOTICE.md) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
本仓库是面向 StrangeUtaGame 的后开发集成项目，不是上游应用，也不重新分发上游
应用。使用者必须自行取得录音、歌词、封面、字体、模型和最终分发权利；FFmpeg
的条款取决于具体构建配置，分发前应查阅其法律说明。
