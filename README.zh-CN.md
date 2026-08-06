[English](README.md) | 简体中文

# Karaoke AV1 Video Production Skill

这是一个面向Codex的卡拉OK视频制作Skill，同时提供经过脱敏的StrangeUtaGame集成，用于制作、审核、渲染、验证和打包带有可编辑时间轴来源和AV1 4:2:0发布检查的卡拉OK视频。

内置默认语言profile为日语（`ja`）；其他语言可通过经过单独验证的适配器接入。

请先阅读[SKILL.md](SKILL.md)；[English README](README.md)以及下方的中英文参考文档会同步维护。

## 包含内容

- `SKILL.md`中的检查→预览→编码→验证流程。
- 语义分段、注音词边界QA、可编辑SUG一致性、MMS与独立ASR证据以及歌词视觉适配门禁。
- 宽屏`vinyl`和`spectrum`模板；每次渲染只能选择一个。
- 默认发布视频为1920x1080、30fps、yuv420p、BT.709：AV1 NVENC CQ38、固定preset p7、tune hq、VBR、全分辨率multipass、lookahead32、空间与时间AQ、strength8、GOP240；默认兼容MP4音频为AAC-LC 320 kb/s，选定源确实无损时可另提供无损音频版。
- 通过`scripts/pitch_shift_audio.py`处理完整混音，默认使用带共振峰保持的Rubber Band R3 Finer；正式流程拒绝MP3/AAC源，不能把有损音频重新标记为FLAC。
- 当前要求并验证的版本为StrangeUtaGame 1.4.5和SUG存储格式0.3.0；`__version__.py`与`pyproject.toml`中的应用版本必须一致。
- 19个不同的脱敏生产入口脚本，另加共享的`sug_ruby.py`规范事实模块。变调工具另在顶层`scripts/pitch_shift_audio.py`保留一份完全相同的独立入口；此外还包含带保护的安装器、编辑器/音频探针、环境检查器、只读的顶层`scripts/check_sug_compatibility.py`验证器、清单和私有覆盖示例。

依赖清单的`scripts`数组包含19个入口；共享模块`sug_ruby.py`单独记录在`shared_modules`中，不另算入口。

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
$projectRoot = (Resolve-Path .\private-project).Path
python scripts/install_strangeutagame_integration.py --target $projectRoot --dry-run
python scripts/install_strangeutagame_integration.py --target $projectRoot
```

创建目标工作树的项目本地环境：

```powershell
$projectRoot = (Resolve-Path .\private-project).Path
Set-Location $projectRoot
winget install astral-sh.uv
uv python install 3.12
uv venv --python 3.12
uv pip install -r requirements-karaoke.skill.lock.txt
```

另行安装`ffmpeg`/`ffprobe`并提供具有合法使用权的CJK字体。Rubber Band仅在升降调时需要；Whisper/MMS和外部MSST是可选证据流程。运行：

```powershell
$projectRoot = (Resolve-Path .\private-project).Path
python scripts/check_karaoke_environment.py --target $projectRoot
```

官方链接、脚本路由、私有清单和网络边界见[集成说明](references/strangeutagame-integration.zh-CN.md)。

## 制作规则

1. 为录音、歌词、同步/展示、字体、封面、模型和最终分发建立权利清单；任何必要权利缺失或不确定时停止公开交付。
2. 探测每个输入并在编码前确定输出矩阵。所有强制门禁通过前保留源媒体并写入临时输出。
3. 文档化的ASR和对齐流程使用已配置的语言 profile；任何非默认 profile 都必须有经过验证的 adapter。独立ASR是独立证据链，绝不是强制对齐失败后的静默后备；不可用或失败时记录为`unresolved`。
4. 请求升降调时，在时间轴和渲染前处理完整混音。验证后的变调FLAC同时用于时间证据、预览、MP4 AAC-LC 320 kb/s和配对MKV FLAC音轨。
5. 默认使用发布视频参数：AV1 NVENC CQ38、preset p7、tune hq、VBR、全分辨率multipass、lookahead32、空间与时间AQ、strength8、GOP240、1920x1080、30fps、yuv420p、BT.709。MP4保持AAC-LC/320k兼容音频；只有源确实无损时才另产无损版。
6. 把MP4和MKV作为同一代一起验证：MP4为AAC-LC/320k，MKV只有FLAC音频，编码视频流哈希一致，时间轴一致，MKV解码PCM等于选定的无损源切片。
7. 候选注音生成器只填补缺失项并先写入规范SUG，保护已有人工或legacy注音。Agent按整句歌词、语法、词形、词边界和上下文自动审核每条注音，可自动批准或直接回写修正；无修改时沿用默认注音并记录批准状态。仅歧义、专名/艺术读音、证据冲突、低置信或`unresolved`升级人工。renderer只读审核后的规范SUG，渲染阶段禁止再推断或覆盖。审核sidecar必须匹配当前SUG哈希，并为每个已存注音span提供范围精确的已批准记录；缺失、陈旧、仅机器填补、低置信、冲突或未解决记录一律失败关闭。发布时先原子写入SUG，再原子写入sidecar，避免中断后sidecar错误证明一个从未落盘的SUG。SUG、ASS/报告和最终帧必须一致，并为每个span记录状态、置信度、evidence、model/prompt版本及SUG修改前后哈希。
8. 保持源文本、适用的注音和上下文读音从可编辑SUG到ASS及渲染输出的可追溯性。
9. 以实际安装应用和`SugMigrator.CURRENT_VERSION`为准；不要把过时的`pyproject.toml`包版本当作SUG契约。

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
$env:KARAOKE_ALBUM_MANIFEST = (Resolve-Path .\private\album.json).Path
uv run python scripts/karaoke_timing.py --manifest $env:KARAOKE_ALBUM_MANIFEST --allow-partial-manifest
```

歌曲专用的显示、注音和上下文读音决定保存在私有JSON中，使用`KARAOKE_DISPLAY_OVERRIDES`、`KARAOKE_RUBY_GROUP_OVERRIDES`和`KARAOKE_TIMING_READING_OVERRIDES`指定。注音覆盖只用于已批准例外或升级案例，保护已有人工或legacy注音，并在渲染前合并回规范SUG。网络访问默认关闭；刷新歌词源和获取公开封面都必须显式授权。

## 脚本来源与依赖边界

19个生产入口脚本都是后来开发的集成脚本；共享的`sug_ruby.py`模块单独记录在`shared_modules`中，不属于入口。它们在脱敏前是生产工作树中的未跟踪新增文件，不是从StrangeUtaGame上游Git历史取出的文件。“直接依赖上游模块”表示导入另行获取的应用中由Git跟踪的模块；“传递运行依赖”表示通过其他集成脚本加载这些模块。

| 脚本 | 边界 | 用途或依赖 |
|---|---|---|
| `karaoke_timing.py` | 直接依赖上游模块 | 领域实体、导出器和`SugProjectParser`。 |
| `karaoke_review_preview.py` | 直接依赖上游模块 | `Sentence`和`SugProjectParser`。 |
| `sync_karaoke_editable_ruby.py` | 传递运行依赖 | SUG-first Agent审核流程：不带`--patches`时只做只读结构审计且不写入；显式传入审核patch JSON后，才把已接受的注音修改回写规范SUG和同名`.ruby-review.json` sidecar。sidecar可能含歌词片段和generation ID，已由Git忽略，必须保留在私有环境。 |
| `sug_ruby.py` | 共享模块；写回时直接依赖上游 | 规范SUG注音校验、哈希、sidecar记录和惰性候选辅助；对象写回会动态导入`Character`和`Sentence`。单独记录在`shared_modules`，不是入口脚本，renderer只读取SUG中已存注音。 |
| `audit_karaoke_asr_recognition.py` | 传递运行依赖 | LRC工具和应用支持的时间轴。 |
| `audit_karaoke_mms_alignment.py` | 传递运行依赖 | 时间轴工具和SUG证据。 |
| `render_karaoke_direct_av1_album.py` | 传递运行依赖 | 通过SUG预览路径重新生成ASS。 |
| `render_karaoke_direct_hevc444_album.py` | 传递运行依赖 | 委托直接AV1渲染器。 |
| `render_karaoke_direct_av1_420_album.py` | 传递运行依赖 | 读取审核后的规范SUG进行注音同步和SUG预览渲染；不得推断或覆盖注音。 |
| `finalize_karaoke_release.py` | SUG成品/目录依赖 | 检查`.sug`文件和发布目录。 |
| `karaoke_album.py` | 不导入上游代码 | 脱敏清单和路径模型。 |
| `karaoke_language.py` | 不导入上游代码 | 语言规范化和已验证profile门禁。 |
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
