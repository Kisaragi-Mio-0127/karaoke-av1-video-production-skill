# StrangeUtaGame集成

[English integration reference](strangeutagame-integration.md) | 中文

本参考说明兼容StrangeUtaGame工作区使用的公开日文/通用集成，覆盖安装器、不会主动发起网络请求的环境检查和显式Bootstrap边界。公开包不添加中文或英文工作流入口。

## 安装

先预览复制计划，再安装到已有工作区：

```powershell
$skillRoot = (Resolve-Path .).Path
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target <StrangeUtaGame> --dry-run
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target <StrangeUtaGame> --force
```

安装器检查项目布局，只复制`integration/strangeutagame/dependency-manifest.json`授权的路径。目标文件不同时必须使用`--force`，并会生成回滚备份。安装器不会创建或替换目标Python环境。

## 运行时选择

通过`uv run --no-sync`使用目标工作区唯一的`.venv`。公开运行时约定使用`--device auto`，使工作流跟随Bootstrap选出的CUDA/CPU能力。目标策略需要固定后端时，显式使用`--device cuda`或`--device cpu`。

生产命令使用项目自有的`models/mms/model.pt`和`models/whisper`，不会隐式下载模型文件。缺少运行时输入时，请先执行下方的显式Bootstrap，不要让生产渲染兼任安装器。

## FFmpeg与FFprobe

默认支持基线是同一套构建中的FFmpeg/FFprobe 8.x；当前验证包为Gyan FFmpeg 8.0.1 Essentials。Windows放在项目专用目录：

```text
<StrangeUtaGame>/tools/ffmpeg/bin/ffmpeg.exe
<StrangeUtaGame>/tools/ffmpeg/bin/ffprobe.exe
```

下载固定的[Gyan FFmpeg 8.0.1 Essentials压缩包](https://github.com/GyanD/codexffmpeg/releases/download/8.0.1/ffmpeg-8.0.1-essentials_build.zip)，解压后把其中`bin`目录的两个文件复制到上述位置。默认安装不要使用会自动跳到新主版本的`ffmpeg-release`链接。FFmpeg 9.x属于显式兼容性迁移，必须先通过NVENC短编码探测。然后在目标项目根目录验证：

```powershell
tools\ffmpeg\bin\ffmpeg.exe -hide_banner -version
tools\ffmpeg\bin\ffprobe.exe -hide_banner -version
tools\ffmpeg\bin\ffmpeg.exe -hide_banner -filters | Select-String 'subtitles|ass'
tools\ffmpeg\bin\ffmpeg.exe -hide_banner -encoders | Select-String 'av1_nvenc|libaom-av1|aac'
```

统一解析顺序为：显式`--ffmpeg`/`--ffprobe`、`FFMPEG`/`FFPROBE`环境变量、项目专用工具、系统`PATH`，最后才把imageio-ffmpeg作为仅FFmpeg的兼容回退。imageio-ffmpeg不提供FFprobe。FFprobe只读取容器与媒体流信息，不负责渲染、编码或修改文件。

公开运行时仅包含日文和通用流程。中文或英文工作流请使用独立的本地Skill。

## 不主动发起网络请求的检查

`scripts/check_karaoke_environment.py`检查本地状态，但不会主动发起网络请求。这不等同于操作系统级断网保证：它会探测本地命令、目标`.venv`、NVIDIA/CPU能力、Python模块和项目自有模型文件。

从公开Skill仓库运行：

```powershell
python scripts/check_karaoke_environment.py --target <StrangeUtaGame>
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --deep-verify
```

默认使用内置Bootstrap清单。非内置的`--manifest <custom-manifest>`必须同时使用`--allow-custom-manifest`；自定义模型URL仍受内置HTTPS主机允许列表限制。普通检查默认只验证配置模型的文件大小，不会读取每个大模型的完整内容；`--deep-verify`才会读取每个完整模型并校验SHA-256。需要在机器外分享JSON报告时可使用`--redact-paths`隐藏绝对本地路径。

即使外部工具缺失导致`core_ok`失败，报告仍可能表明Python/模型环境可用。请单独安装`git`、`uv`、`ffmpeg`和`ffprobe`；Bootstrap不管理这些工具，也不管理GPU驱动。

## 显式Bootstrap

Bootstrap是显式设置命令。它探测NVIDIA/CPU，复用或创建唯一的`<target>/.venv`，安装版本固定的Python包，并把缺失的MMS/Whisper文件下载到`<target>/models/`。它不会创建第二个环境。

先查看计划：

```powershell
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --dry-run
```

`--dry-run`会深度校验已有模型并规划动作，但不会写入或主动发起网络请求。确认设置后，执行显式命令：

```powershell
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --accept-mms-cc-by-nc-4-0
```

缺失MMS检查点开始下载前必须提供MMS选项。它确认CC BY-NC 4.0的署名和非商业用途要求；下载会在检查点旁写入来源/许可证sidecar。使用自定义清单时还必须加`--allow-custom-manifest`。没有合适的本地Python时，只有追加`--allow-python-download`才允许uv下载托管解释器；默认会拒绝这类Python下载。

只有在所需包和模型已经位于本地缓存时才使用`--offline`。它会阻止模型和Python下载并让uv使用离线模式。Bootstrap从不安装或更新`git`、`uv`、`ffmpeg`、`ffprobe`或GPU驱动。

## 项目配置

使用已授权的清单和冻结歌词源。选定歌曲、音频、字体、模型路径和新的私有输出目录都必须在生产开始前存在。保持规范SUG、冻结歌词、私有证据、companion SUG和交付媒体彼此分离。

默认日文流程使用项目自有的MMS和Whisper路径。生产CLI可以显式覆盖路径，但覆盖不授权网络下载。注音验证仍是可选项。日文分阶段、直接渲染和批量CLI提供`--pronunciation-validation {off,optional,required}`，默认是`optional`；full-auto不要求这个sidecar。

## 生产顺序

公开日文生产顺序如下：

```text
manifest + song-id + frozen lyric source + new output directory
-> MSST -> private initial SUG -> Japanese MMS
-> editable companion SUG -> current layout -> AV1 MP4
```

每次full-auto或分阶段运行都需要新的私有输出目录。以已安装命令的`--help`输出为最终参数依据。公开运行时约定使用`--device auto`；只有需要固定后端时才显式传入`--device cuda`或`--device cpu`。

## 日文Full-auto入口

从StrangeUtaGame项目根目录运行通常的第一条命令：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py --manifest <manifest> --song-id <song-id> --source <frozen-lyrics.json> --output-dir .render-work/<new-run-dir> --device auto
```

该命令准备MSST人声，生成私有初始SUG，运行日文MMS，生成可编辑companion，准备当前布局并渲染AV1 MP4。默认质量策略是`auto-fallback`，默认视觉样式是`spectrum`。低置信度回退证据会保留在报告中；人工或Agent校轴是可选项。

## 日文分阶段MMS入口

需要审计、恢复或检查阶段时使用分阶段包装器：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py --manifest <manifest> --song-id <song-id> --source <frozen-lyrics.json> --mms-model-path models/mms/model.pt --quality-policy auto-fallback --output-dir <new-private-output-dir> --visual-style spectrum --device auto
```

必需参数是`--manifest`、`--song-id`和新的`--output-dir`。`--source`、`--sug`和`--vocals-root`是可选覆盖项；省略时由项目清单默认值解析选定输入。包装器分离保存审计、构建、companion和渲染产物，不替换规范SUG，也不会静默下载缺失的MMS检查点。

`--allow-mms-network`虽然出现在帮助中，但它不是Bootstrap的替代品，也不是公开本地模型契约所需的选项。模型准备必须保持显式且本地化。

## 已有SUG重新渲染与批量入口

已有调整后或复核后的SUG使用直接重新渲染入口。它不运行MSST或MMS：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py --sug <adjusted-project.sug> --audio <post-mix-audio> --output-dir <new-output-dir> --title <title> --artist <artist> --album-title <album-title> --album-artist <album-artist> --visual-style spectrum
```

从已复核时间轴批量渲染：

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py --manifest <manifest> --visual-style spectrum
```

批量入口不会调用MMS，也不会生成时间覆盖。提升交付前请验证已有的时间证据。

## 布局与交付

当前宽屏构图、spectrum/vinyl选择和次级覆盖规则只在[宽屏视觉模板](wide-visual-templates.zh-CN.md)中定义。本集成参考只保留高层说明，不复制几何常数。

默认交付是带硬字幕和AAC-LC音频的AV1`yuv420p`MP4。MKV/FLAC和完整空解码都是显式选项。提升交付前执行字幕、流、时长和代表帧门禁；详见[AV1 4:2:0命令](av1-420-commands.zh-CN.md)和[批量发布门禁](batch-release-gates.zh-CN.md)。

## 各脚本对上游StrangeUtaGame的依赖

这些集成文件是在StrangeUtaGame之外后续开发的，不属于其上游Git历史，本仓库也不拥有或包含上游`strange_uta_game`源码。下表中的“可独立运行”是指不依赖上游StrangeUtaGame代码和工作区资源，并不表示不依赖本集成自己的Python包、输入媒体、字体、FFmpeg、模型或其他明确列出的工具。

安装器把清单授权的所有Python路径安装到`<target>/scripts/`，包括`karaoke_common`、`karaoke_japanese`和`karaoke_zh_en`包目录；两份requirements文件安装到目标根目录。上游包仍位于`<target>/src/strange_uta_game/`，固定版本requirements中的本地`-e .`条目使其可从目标唯一的`.venv`导入。MMS和Whisper资产是位于`<target>/models/`下的独立项目自有运行时文件。

### 直接或传递依赖上游代码

| 脚本/模块 | 依赖的上游模块、资源或运行时 | 安装位置 | 可脱离上游独立运行？ |
|---|---|---|---|
| `karaoke_timing.py` | 直接导入`backend.application.auto_check_service`、`backend.domain`、`backend.infrastructure.exporters`和`backend.infrastructure.persistence.sug_io`；还使用SUG项目、字体、Whisper/stable-ts和FFmpeg。 | `<target>/scripts/karaoke_timing.py` | 否。 |
| `karaoke_review_preview.py` | 直接导入上游`Character`、`Sentence`和`SugProjectParser`；还导入`karaoke_timing.py`并调用FFmpeg完成预览/渲染。 | `<target>/scripts/karaoke_review_preview.py` | 否。 |
| `karaoke_mms_editable.py` | 直接从上游SUG持久化模块导入`SugProjectParser`，读取并写入SUG companion。 | `<target>/scripts/karaoke_mms_editable.py` | 否。 |
| `sug_ruby.py` | 对象回写路径动态导入上游`Ruby`和`RubyPart`；仅检查和验证原始JSON的路径不需要该导入。 | `<target>/scripts/sug_ruby.py` | 部分可以：仅JSON验证可脱离上游，对象回写不可。 |
| `audit_karaoke_asr_recognition.py` | 从`karaoke_timing.py`导入LRC/修正辅助函数，因此加载这些函数会初始化上游导入；此外需要项目自有Whisper权重和stable-whisper/torch运行时。 | `<target>/scripts/audit_karaoke_asr_recognition.py` | 受支持的审计路径不可。 |
| `audit_karaoke_mms_alignment.py` | 导入`karaoke_timing.py`和规范ruby辅助函数；读取SUG时间、原始/MSST音频，并通过torchaudio MMS_FA加载本地`models/mms/model.pt`。 | `<target>/scripts/audit_karaoke_mms_alignment.py` | 否。 |
| `build_karaoke_mms_overrides.py` | 从`karaoke_timing.py`导入时间结构/辅助函数，并读取SUG/MMS审计产物。 | `<target>/scripts/build_karaoke_mms_overrides.py` | 否。 |
| `sync_karaoke_editable_ruby.py` | 针对SUG项目数据使用`sug_ruby.py`；规范对象回写路径依赖上游领域类。 | `<target>/scripts/sync_karaoke_editable_ruby.py` | 受支持的集成回写流程不可。 |
| `karaoke_workflow.py` | 使用同一Python解释器导入并启动`karaoke_review_preview.py`，因此继承预览/时间模块的上游导入；还使用目标项目根目录、资产、FFmpeg和发布辅助模块。 | `<target>/scripts/karaoke_workflow.py` | 否。 |
| `render_karaoke_direct_av1_420_album.py` | 每个渲染任务都执行`karaoke_review_preview.py`，因此继承其直接上游解析器/领域依赖；还使用SUG文件、图稿/字体资产和FFmpeg AV1编码器。 | `<target>/scripts/render_karaoke_direct_av1_420_album.py` | 否。 |
| `run_karaoke_japanese_workflow.py` | `karaoke_workflow.py`的轻量入口，继承其预览、SUG、项目布局和FFmpeg依赖。 | `<target>/scripts/run_karaoke_japanese_workflow.py` | 否。 |
| `run_karaoke_japanese_mms_workflow.py` | 导入MMS审计/构建、`karaoke_mms_editable.py`、`karaoke_review_preview.py`和`karaoke_workflow.py`；需要规范/companion SUG、本地MMS模型、音频stem、字体和FFmpeg。 | `<target>/scripts/run_karaoke_japanese_mms_workflow.py` | 否。 |
| `karaoke_full_auto.py` | 导入`karaoke_timing.py`、ASR和MSST准备模块，然后延迟导入日文MMS工作流；需要目标清单/布局、上游SUG运行时、本地MMS/Whisper模型、MSST适配器和FFmpeg。 | `<target>/scripts/karaoke_full_auto.py` | 否。 |
| `run_karaoke_japanese_full_auto.py` | `karaoke_full_auto.py`的日文限定入口，继承完整的时间、MMS、SUG、MSST、模型和渲染依赖链。 | `<target>/scripts/run_karaoke_japanese_full_auto.py` | 否。 |

### 不导入上游代码但依赖工件或布局

| 脚本/模块 | 依赖的上游模块、资源或运行时 | 安装位置 | 可脱离上游独立运行？ |
|---|---|---|---|
| `finalize_karaoke_release.py` | 不导入上游代码，但会验证预期的规范/companion `.sug`工件和集成发布布局；使用统一FFmpeg解析器。 | `<target>/scripts/finalize_karaoke_release.py` | 有条件可以：不依赖上游代码，但依赖已有SUG工件/布局。 |
| `build_karaoke_wide_artwork.py`<br>`karaoke_cover_palette.py`<br>`karaoke_color_plan.py`<br>`karaoke_common/artwork.py` | 不导入上游代码，使用Pillow和本集成输入生成确定性图稿/调色板。 | `<target>/scripts/`下对应路径 | 可以，但需要声明的图片、字体和元数据。 |
| `inspect_karaoke_media.py`<br>`transcode_karaoke_av1.py`<br>`render_vinyl_karaoke.py`<br>`pitch_shift_audio.py` | 不导入上游代码，使用媒体/清单元数据和外部运行时：FFmpeg，以及所选路径中的FFprobe；移调还需要Rubber Band 3.x。 | `<target>/scripts/`下对应路径 | 可以，但需要相应媒体和外部命令。 |
| `prepare_karaoke_msst_vocals.py` | 不导入上游代码；加载外部本地`prepare_sovits41_msst_stems.py`适配器及其MSST运行时/模型文件，这些内容由本集成之外的组件拥有。 | `<target>/scripts/prepare_karaoke_msst_vocals.py` | 相对于StrangeUtaGame可以；相对于独立MSST适配器/运行时不可以。 |
| `karaoke_album.py`<br>`karaoke_language.py`<br>`karaoke_release_snapshot.py`<br>`karaoke_direct_album_planning.py`<br>`package_karaoke_numbered_archives.py` | 不导入上游代码，处理集成清单、路径、快照或发布文件；专辑规划通过统一FFmpeg解析器检查媒体。 | `<target>/scripts/`下对应路径 | 可以，但需要声明的集成输入。 |
| `karaoke_model_paths.py` | 不导入上游代码，只解析项目自有`models/mms/model.pt`和`models/whisper/`路径。 | `<target>/scripts/karaoke_model_paths.py` | 相对于上游代码可以；调用方仍需要模型文件。 |
| `karaoke_common/layout.py`<br>`karaoke_japanese/layout.py`<br>`karaoke_zh_en/layout.py` | 不导入上游代码，是本集成自有布局定义。`karaoke_zh_en`包提供通用预览布局，不提供中文/英文工作流入口。 | `<target>/scripts/`下对应包路径 | 可以；这些是模块，不是独立命令。 |
| `karaoke_common/device.py` | 不导入上游代码，动态加载`torch`选择CPU/CUDA。 | `<target>/scripts/karaoke_common/device.py` | 可以；这是模块，不是独立命令。 |
| `karaoke_common/pronunciation.py` | 不直接导入上游代码，使用`sug_ruby.py`中支持JSON的部分执行注音策略。 | `<target>/scripts/karaoke_common/pronunciation.py` | 验证路径可以；这是模块，不是独立命令。 |
| `karaoke_common/__init__.py`<br>`karaoke_japanese/__init__.py`<br>`karaoke_zh_en/__init__.py` | 仅为包初始化文件，依赖取决于其导出的包成员。 | `<target>/scripts/`下对应包路径 | 相对于上游代码可以；不是独立命令。 |

### Skill侧安装与兼容性工具

这些工具保留在Skill工作区，集成安装器不会把它们复制到目标工作区。

| 工具 | 依赖的上游模块、资源或运行时 | 位置 | 可脱离上游独立运行？ |
|---|---|---|---|
| `install_strangeutagame_integration.py` | 要求兼容目标布局包含`pyproject.toml`、`src/strange_uta_game/`和`scripts/`，只复制清单授权的集成文件。 | `<skill>/scripts/` | 否：需要目标工作区，但不导入上游代码。 |
| `check_sug_compatibility.py` | 从`<target>/src`直接导入上游版本、`SugMigrator`和`SugProjectParser`，读取代表性SUG项目但不保存。 | `<skill>/scripts/` | 否。 |
| `open_editable_project_with_audio_probe.py` | 动态导入上游GUI/应用目录、时间加载器/接口、项目存储、SUG持久化和目标`main`模块；还探测上游音频转换钩子和媒体。 | `<skill>/scripts/` | 否。 |
| `check_karaoke_environment.py`<br>`bootstrap_karaoke_environment.py`<br>`karaoke_bootstrap.py` | 需要兼容目标布局和目标`.venv`；探测或安装清单内Python模块（包括目标自身的可编辑`strange_uta_game`），并管理项目自有模型文件；还探测`git`、`uv`、FFmpeg/FFprobe和硬件运行时。 | `<skill>/scripts/` | 否：用途就是检查/设置目标工作区。 |
| Skill侧`pitch_shift_audio.py` | 无上游依赖，是独立的FFmpeg/FFprobe/Rubber Band 3.x工具。 | `<skill>/scripts/pitch_shift_audio.py` | 可以。 |

## 已安装文件与验证

日文/通用包包含`dependency-manifest.json`列出的授权脚本、语言中立共享模块、包文件、依赖和支持工具。公开仓库的测试不会安装到StrangeUtaGame。

使用实际目标运行兼容性和环境检查：

```powershell
Set-Location <StrangeUtaGame>
uv run --no-sync python D:\path\to\skill\scripts/check_sug_compatibility.py --repo . --project <project.sug>
python D:\path\to\skill\scripts/check_karaoke_environment.py --target .
```

如果`ffmpeg`或`ffprobe`缺失，环境检查可能返回非零；这只是诊断结果，不表示生产流程可以下载这些工具。
