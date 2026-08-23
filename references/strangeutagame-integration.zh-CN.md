# StrangeUtaGame集成

[English integration reference](strangeutagame-integration.md) | 中文

本参考说明兼容StrangeUtaGame工作区的安装、环境准备、生产入口、工作区依赖和验证流程。

`main`分支对应StrangeUtaGame 1.6.2与SUG格式0.3.0。StrangeUtaGame 1.5.0或1.4.5工作区应分别使用版本分支`sug-1.5.0`或`sug-1.4.5`。

## 安装

从上游仓库
[karaoke-studio/StrangeUtaGame](https://github.com/karaoke-studio/StrangeUtaGame)
获取应用，并在本Skill之外单独准备兼容工作区。

先预览复制计划，再安装到已有工作区：

```powershell
$skillRoot = (Resolve-Path .).Path
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target <StrangeUtaGame> --dry-run
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target <StrangeUtaGame> --force
```

安装器检查项目布局，只复制`integration/strangeutagame/dependency-manifest.json`授权的路径。目标文件不同时必须使用`--force`，并会生成回滚备份。安装器不会创建或替换目标Python环境。

## 运行时选择

通过`uv run --no-sync`使用目标工作区唯一的`.venv`。使用`--device auto`，使工作流跟随Bootstrap选出的CUDA/CPU能力。目标策略需要固定后端时，显式使用`--device cuda`或`--device cpu`。

生产命令使用项目自有的`models/mms/model.pt`和`models/whisper`，不会隐式下载模型文件。缺少运行时输入时，请先执行下方的显式Bootstrap，不要让生产渲染兼任安装器。

`local-mms-fa`是默认对齐后端。实验性、仅限日文的NextFire后端只能通过`--mms-backend nextfire-ja-latn`选择，不宣称优于默认后端，且只解析`models/hf/nextfire-mms-ja-latn`。运行时不会下载、不使用通用Hugging Face缓存，也不执行远程代码。

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

## 不主动发起网络请求的检查

`scripts/check_karaoke_environment.py`检查本地状态，但不会主动发起网络请求。这不等同于操作系统级断网保证：它会探测本地命令、目标`.venv`、NVIDIA/CPU能力、Python模块和项目自有模型文件。

从集成仓库运行：

```powershell
python scripts/check_karaoke_environment.py --target <StrangeUtaGame>
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --deep-verify
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --nextfire-mms-ja-latn
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

可选NextFire快照先单独检查计划，再只在同时确认两项许可后安装：

```powershell
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --nextfire-mms-ja-latn --dry-run
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --nextfire-mms-ja-latn --accept-nextfire-agpl-3-0 --accept-mms-cc-by-nc-4-0
```

权重只保存在本地，不提交到本仓库。

缺失MMS检查点开始下载前必须提供MMS选项。它确认CC BY-NC 4.0的署名和非商业用途要求；下载会在检查点旁写入来源/许可证sidecar。使用自定义清单时还必须加`--allow-custom-manifest`。没有合适的本地Python时，只有追加`--allow-python-download`才允许uv下载托管解释器；默认会拒绝这类Python下载。

只有在所需包和模型已经位于本地缓存时才使用`--offline`。它会阻止模型和Python下载并让uv使用离线模式。Bootstrap从不安装或更新`git`、`uv`、`ffmpeg`、`ffprobe`或GPU驱动。

## 项目配置

使用已授权的清单和一种显式歌词输入。选定歌曲、音频、字体、模型路径和新的输出位置必须在生产开始前有效。除非显式使用`--refresh-source`授权单曲刷新，否则歌词源文件必须已经存在；省略`--netease-song-id`时，脚本会读取受支持的音频内嵌网易歌曲ID。手工UTF-8歌词可用`--lyrics-file <lyrics.lrc|lyrics.txt>`替代`--source`；纯文本会生成均匀粗时间锚点并进入时间复核状态。保持源SUG、冻结歌词、工作证据、companion SUG和交付媒体彼此分离。

仅在已显式授权查询专辑详情时运行`scripts/karaoke_netease_metadata.py <audio> --identity --fetch-album`。该命令会访问网易专辑接口，并将专辑作者与音频中的曲目歌手分开报告。

默认日文流程使用项目自有的MMS和Whisper路径。生产CLI可以显式覆盖路径，但覆盖不授权网络下载。注音验证仍是可选项。日文分阶段、直接渲染和批量CLI提供`--pronunciation-validation {off,optional,required}`，默认是`optional`；full-auto不要求这个sidecar。

## 生产顺序

日文生产顺序如下：

```text
manifest + song-id + one lyric source + new output directory
-> MSST -> working initial SUG -> Japanese MMS
-> editable companion SUG -> current layout -> AV1 MP4
-> relocatable editable SUG
```

每次full-auto或分阶段运行都需要新的输出目录。以已安装命令的`--help`输出为最终参数依据。运行时约定使用`--device auto`；只有需要固定后端时才显式传入`--device cuda`或`--device cpu`。

## 日文Full-auto入口

从StrangeUtaGame项目根目录运行通常的第一条命令：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py --manifest <manifest> --song-id <song-id> --source <frozen-lyrics.json> --output-dir .render-work/<new-run-dir> --device auto
```

使用手工歌词时，将`--source <frozen-lyrics.json>`替换为`--lyrics-file <lyrics.lrc|lyrics.txt>`。

需要选择实验性、仅限日文的后端时，加入`--mms-backend nextfire-ja-latn`。常规双音轨审核以及`auto-fallback`/`strict`策略仍然生效。

该命令准备MSST人声，生成工作初始SUG，运行日文MMS，生成可编辑companion，准备当前布局并渲染AV1 MP4。默认质量策略是`auto-fallback`，默认视觉样式是`spectrum`。低置信度回退证据会保留在报告中；人工或Agent校轴是可选项。

Full-auto或分阶段MMS命令可加入`--output-mode subtitle-overlay`，MMS各阶段保持原有流程，仅更改最终渲染。省略视频素材时生成无音频、带透明通道的ProRes 4444 MOV；加入`--background-video <视频素材>`时由FFmpeg直接合成为AV1/AAC，较长素材裁剪到歌曲区间，较短素材的剩余区间显示黑幕。背景视频路径依次探测`av1_nvenc`和`libaom-av1`，硬件初始化或渲染失败时自动改用软件编码器。

专辑显示信息默认读取音频标签，再回退到歌曲名和歌手；变调或无标签音频可用`--metadata-source-audio`指定原始带标签音频。

## 日文分阶段MMS入口

需要审计、恢复或检查阶段时使用分阶段包装器：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py --manifest <manifest> --song-id <song-id> --source <frozen-lyrics.json> --mms-model-path models/mms/model.pt --quality-policy auto-fallback --output-dir <new-output-dir> --visual-style spectrum --device auto
```

需要显式使用实验性后端时，以`--mms-backend nextfire-ja-latn`代替`--mms-model-path`。双音轨审核和质量策略不变。

必需参数是`--manifest`、`--song-id`和新的`--output-dir`。`--source`、`--sug`和`--vocals-root`是可选覆盖项；省略时由项目清单默认值解析选定输入。包装器分离保存审计、构建、companion和渲染产物，不替换规范SUG，也不会静默下载缺失的MMS检查点。

`--allow-mms-network`虽然出现在帮助中，但它不是Bootstrap的替代品，也不是本地模型契约所需的选项。模型准备必须保持显式且本地化。

## 已有SUG重新渲染与批量入口

已有调整后或复核后的SUG使用直接重新渲染入口。它不运行MSST或MMS：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py --sug <adjusted-project.sug> --audio <post-mix-audio> --output-dir <new-output-dir> --title <title> --artist <artist> --visual-style spectrum
```

此入口同样接受`--output-mode subtitle-overlay`和可选的`--background-video`。

专辑参数只用于显式覆盖。每次成功的直接重渲染或full-auto都会包含`editable-project/<名称>.sug`，并校验其中的媒体路径。

从已复核时间轴批量渲染：

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py --manifest <manifest> --visual-style spectrum
```

批量入口不会调用MMS，也不会生成时间覆盖。提升交付前请验证已有的时间证据。

## 布局与交付

当前宽屏构图、spectrum/vinyl选择和次级覆盖规则只在[宽屏视觉模板](wide-visual-templates.zh-CN.md)中定义。本集成参考只保留高层说明，不复制几何常数。

默认交付是带硬字幕和AAC-LC音频的AV1`yuv420p`MP4。MKV/FLAC和完整空解码都是显式选项。提升交付前执行字幕、流、时长和代表帧门禁；详见[AV1 4:2:0命令](av1-420-commands.zh-CN.md)和[批量发布门禁](batch-release-gates.zh-CN.md)。

## 工作区依赖与兼容性

安装器只复制[`dependency-manifest.json`](../integration/strangeutagame/dependency-manifest.json)列出的路径：生产脚本和共享包进入`<target>/scripts/`，requirements文件进入目标根目录。安装与生产流程统一使用目标工作区的`.venv`。

Full-auto、分阶段MMS、直接重渲染和批量渲染需要目标应用运行时、SUG领域模型、`SugMigrator`、解析与持久化支持，以及选定的清单、媒体、字体、FFmpeg/FFprobe和声明的MMS/Whisper模型。媒体、美术、打包与模型路径辅助模块即使不导入应用代码，也需要各自文档列出的输入和外部工具。

`install_strangeutagame_integration.py`、`check_karaoke_environment.py`和`bootstrap_karaoke_environment.py`保留在集成仓库，并通过`--target`操作目标工作区。兼容性检查会导入目标运行时的`__version__`、`SugMigrator`和`SugProjectParser`，随后读取代表性SUG工程且不保存。实际解析结果还须与安装器内置的应用版本和SUG格式精确检查共同通过；单独解析成功不授权安装。

## 已安装文件与验证

已安装集成包含[`dependency-manifest.json`](../integration/strangeutagame/dependency-manifest.json)列出的授权脚本、共享模块、包文件、依赖和支持工具。仓库测试不会安装到StrangeUtaGame。

使用实际目标运行兼容性和环境检查：

```powershell
Set-Location <StrangeUtaGame>
uv run --no-sync python <skill-root>\scripts\check_sug_compatibility.py --repo . --project <project.sug>
python <skill-root>\scripts\check_karaoke_environment.py --target .
```

如果`ffmpeg`或`ffprobe`缺失，环境检查可能返回非零；这只是诊断结果，不表示生产流程可以下载这些工具。
