[English](README.md) | 简体中文

# Karaoke AV1 Video Production Skill

这是一个面向Codex的卡拉OK视频制作Skill，同时提供经过脱敏的StrangeUtaGame集成，用于制作、审核、渲染、验证和打包带有可编辑时间轴来源和AV1 4:2:0发布检查的卡拉OK视频。

请先阅读[SKILL.md](SKILL.md)；[English README](README.md)以及下方的中英文参考文档会同步维护。

## 包含内容

- `SKILL.md`中的检查→预览→编码→验证流程。
- 语义分段、日语注音词边界QA、可编辑SUG一致性、MMS与独立ASR证据以及CJK视觉适配门禁。
- 宽屏`vinyl`和`spectrum`模板；每次渲染只能选择一个。
- 默认MP4交付，音频为AAC-LC 320 kb/s；选定源确实是无损FLAC或PCM WAV时，另提供配对的FLAC音频MKV。
- 通过`scripts/pitch_shift_audio.py`处理完整混音，默认使用带共振峰保持的Rubber Band R3 Finer；正式流程拒绝MP3/AAC源，不能把有损音频重新标记为FLAC。
- 当前已验证基线为StrangeUtaGame 1.4.5和SUG存储格式0.3.0。`pyproject.toml`中的包版本可能仍为1.2.6；它不是应用版本或解析器格式的真源。
- 20个不同的脱敏生产脚本实现。变调工具另在顶层`scripts/pitch_shift_audio.py`保留一份完全相同的独立入口；此外还包含带保护的安装器、编辑器/音频探针、环境检查器、只读的顶层`scripts/check_sug_compatibility.py`验证器、清单和私有覆盖示例。

仓库不包含录音、歌词、专辑信息、字体、封面、模型、凭据、渲染媒体或真实项目报告。

## 安装Skill与集成

将公开仓库克隆到Codex技能目录：

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

在Codex中调用：

```text
$karaoke-av1-video-production
```

集成依赖已获授权的StrangeUtaGame工作树。先预览复制计划，再安装：

```powershell
python scripts/install_strangeutagame_integration.py --target D:\path\to\StrangeUtaGame --dry-run
python scripts/install_strangeutagame_integration.py --target D:\path\to\StrangeUtaGame
```

创建目标工作树的项目本地环境：

```powershell
Set-Location D:\path\to\StrangeUtaGame
winget install --id=astral-sh.uv -e
uv python install 3.12
uv venv --python 3.12
uv pip install -r requirements-karaoke.skill.lock.txt
```

另行安装`ffmpeg`/`ffprobe`并提供具有合法使用权的CJK字体。Rubber Band仅在升降调时需要；Whisper/MMS和外部MSST是可选证据流程。运行：

```powershell
python scripts/check_karaoke_environment.py --target D:\path\to\StrangeUtaGame
```

官方链接、脚本路由、私有清单和网络边界见[集成说明](references/strangeutagame-integration.zh-CN.md)。

## 制作规则

1. 为录音、歌词、同步/展示、字体、封面、模型和最终分发建立权利清单；任何必要权利缺失或不确定时停止公开交付。
2. 探测每个输入并在编码前确定输出矩阵。所有强制门禁通过前保留源媒体并写入临时输出。
3. 每次ASR/对齐运行都必须恰好选择一种语言：`ja`、`zh`或`en`。独立ASR是独立证据链，绝不是强制对齐失败后的静默后备；不可用或失败时记录为`unresolved`。繁简转换只用于`zh`内部比较，不是语言后备。
4. 请求升降调时，在时间轴和渲染前处理完整混音。验证后的变调FLAC同时用于时间证据、预览、MP4 AAC-LC 320 kb/s和配对MKV FLAC音轨。
5. 把MP4和MKV作为同一代一起验证：MP4为AAC-LC/320k，MKV只有FLAC音频，编码视频流哈希一致，时间轴一致，MKV解码PCM等于选定的无损源切片。
6. 不要为了逐字计时或显示分段在中文歌词汉字之间插入空格；使用词义和声学证据。
7. 以实际安装应用和`SugMigrator.CURRENT_VERSION`为准；不要把过时的`pyproject.toml`包版本当作SUG契约。

## 参考文档

每份英文参考都有对应中文文件，并且相互链接：

| 主题 | English | 中文 |
|---|---|---|
| AV1、FFmpeg、MP4/MKV | [av1-420-commands.md](references/av1-420-commands.md) | [av1-420-commands.zh-CN.md](references/av1-420-commands.zh-CN.md) |
| SUG、独立ASR、变调 | [asr-sug-pitch.md](references/asr-sug-pitch.md) | [asr-sug-pitch.zh-CN.md](references/asr-sug-pitch.zh-CN.md) |
| 宽屏黑胶/频谱 | [wide-visual-templates.md](references/wide-visual-templates.md) | [wide-visual-templates.zh-CN.md](references/wide-visual-templates.zh-CN.md) |
| 字幕时间轴与质量 | [subtitle-timing-quality.md](references/subtitle-timing-quality.md) | [subtitle-timing-quality.zh-CN.md](references/subtitle-timing-quality.zh-CN.md) |
| 批量发布 | [batch-release-gates.md](references/batch-release-gates.md) | [batch-release-gates.zh-CN.md](references/batch-release-gates.zh-CN.md) |
| StrangeUtaGame集成 | [strangeutagame-integration.md](references/strangeutagame-integration.md) | [strangeutagame-integration.zh-CN.md](references/strangeutagame-integration.zh-CN.md) |

## 私有项目数据

将`examples/album.example.json`复制到私有项目目录，替换全部占位信息并显式传入：

```powershell
$env:KARAOKE_ALBUM_MANIFEST = "D:\private\album.json"
uv run python scripts/karaoke_timing.py --manifest $env:KARAOKE_ALBUM_MANIFEST --allow-partial-manifest
```

歌曲专用的显示、注音和上下文读音决定保存在私有JSON中，使用`KARAOKE_DISPLAY_OVERRIDES`、`KARAOKE_RUBY_GROUP_OVERRIDES`和`KARAOKE_TIMING_READING_OVERRIDES`指定。网络访问默认关闭；刷新歌词源和获取公开封面都必须显式授权。

## 脚本来源与依赖边界

20个生产脚本都是后来开发的集成脚本。它们在脱敏前是生产工作树中的未跟踪新增文件，不是从StrangeUtaGame上游Git历史取出的文件。“直接依赖上游模块”表示导入另行获取的应用中由Git跟踪的模块；“传递运行依赖”表示通过其他集成脚本加载这些模块。

| 脚本 | 边界 | 用途或依赖 |
|---|---|---|
| `karaoke_timing.py` | 直接依赖上游模块 | 领域实体、导出器和`SugProjectParser`。 |
| `karaoke_review_preview.py` | 直接依赖上游模块 | `Character`、`Sentence`和`SugProjectParser`。 |
| `convert_english_sug_word_tokens.py` | 直接依赖上游模块 | `SugProjectParser`和SUG时间轴转换。 |
| `sync_karaoke_editable_ruby.py` | 传递运行依赖 | 上下文注音和专辑时间轴数据。 |
| `audit_karaoke_asr_recognition.py` | 传递运行依赖 | LRC工具和应用支持的时间轴。 |
| `audit_karaoke_mms_alignment.py` | 传递运行依赖 | 时间轴工具和SUG证据。 |
| `render_karaoke_direct_av1_album.py` | 传递运行依赖 | 通过SUG预览路径重新生成ASS。 |
| `render_karaoke_direct_hevc444_album.py` | 传递运行依赖 | 委托直接AV1渲染器。 |
| `render_karaoke_direct_av1_420_album.py` | 传递运行依赖 | 注音同步和SUG预览渲染。 |
| `finalize_karaoke_release.py` | SUG成品/目录依赖 | 检查`.sug`文件和发布目录。 |
| `karaoke_album.py` | 不导入上游代码 | 脱敏清单和路径模型。 |
| `karaoke_language.py` | 不导入上游代码 | 语言规范化和分词。 |
| `build_karaoke_wide_artwork.py` | 不导入上游代码 | 使用Pillow构图。 |
| `render_vinyl_karaoke.py` | 不导入上游代码 | 生成黑胶视觉层。 |
| `inspect_karaoke_media.py` | 不导入上游代码 | 检查编码媒体和渲染元数据。 |
| `transcode_karaoke_av1.py` | 不导入上游代码 | 使用FFmpeg元数据转码和验证。 |
| `prepare_karaoke_msst_vocals.py` | 不导入上游代码 | 准备可选外部MSST证据。 |
| `package_karaoke_numbered_archives.py` | 不导入上游代码 | 生成编号发布归档。 |
| `karaoke_release_snapshot.py` | 不导入上游代码 | 创建发布文件快照。 |
| `pitch_shift_audio.py` | 不导入上游代码 | 处理完整混音并生成变调验证报告。 |

音频探针会动态导入应用的GUI、持久化和音频加载模块；安装器和环境检查器操作现有工作树但不导入应用代码。权威清单为`integration/strangeutagame/dependency-manifest.json`。

## 仓库结构与测试

```text
.
├── SKILL.md
├── LICENSE
├── NOTICE.md
├── THIRD_PARTY_NOTICES.md
├── agents/                  # 打包元数据
├── examples/                # 通用私有数据示例
├── integration/strangeutagame/
│   ├── dependency-manifest.json
│   ├── requirements/
│   └── scripts/
├── references/
├── scripts/
│   ├── check_karaoke_environment.py
│   ├── check_sug_compatibility.py
│   ├── install_strangeutagame_integration.py
│   ├── open_editable_project_with_audio_probe.py
│   └── pitch_shift_audio.py
└── tests/
```

```powershell
python -m unittest discover -s scripts -p "test_*.py" -v
python -m unittest discover -s tests -p "test_*.py" -v
```

这些是打包与安全测试，不代表没有私有素材时能完成整首媒体渲染。正式制作前运行环境检查、全部命令的帮助烟测、授权短预览和发布门禁。

## 许可证与权利

仓库随附的`LICENSE`明确本仓库代码和文档使用GPL-3.0-only。详见[NOTICE.md](NOTICE.md)和[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本仓库是后来为[karaoke-studio/StrangeUtaGame](https://github.com/karaoke-studio/StrangeUtaGame)开发的集成项目，上游仓库声明使用GPL-3.0。本仓库不是上游应用，也不重新分发该应用。使用者必须自行取得录音、歌词、封面、字体、模型和最终分发权；FFmpeg条款取决于具体构建配置，发布前应查看其法律说明。
