# 卡拉OK AV1视频制作Skill

[English](README.md)

Karaoke AV1 Video Production为StrangeUtaGame提供日文卡拉OK时间轴、可编辑SUG工程、宽屏字幕渲染和AV1交付流程。歌曲元数据来自专辑清单，歌词文本来自冻结JSON或手工提供的LRC/TXT输入。

## 兼容性检查

安装前对目标StrangeUtaGame工作区运行兼容性检查与安装器dry run。兼容性依据应用运行时版本、`SugMigrator` schema和代表性SUG解析结果判断。解析成功仍须通过安装器内置的应用版本与SUG格式精确检查；安装器会在复制文件前报告不受支持的目标。

`main`分支对应StrangeUtaGame 1.6.2与SUG格式0.3.0。旧应用版本的精确集成分别保留在`sug-1.5.0`和`sug-1.4.5`分支。

## 可以自动完成什么

推荐的日文入口是单命令`scripts/run_karaoke_japanese_full_auto.py`。给定清单、歌曲ID、歌词输入和新的输出目录后，它会：

- 准备选中歌曲的MSST人声分轨；
- 生成工作初始SUG；
- 运行日文MMS并生成可编辑的companion SUG；
- 准备当前布局并渲染AV1 MP4交付物；
- 导出一份媒体路径已经校验、移动后仍可继续调轴的SUG。

默认质量策略是`auto-fallback`。流程采用可用的高置信度MMS时间，同时让低置信度或未解决单元保留规范时间，并在报告中保留证据。companion SUG生成后，人工或Agent校轴是可选后续，不是自动流程的前置条件。

默认使用冻结歌词源。只有显式加入`--refresh-source`时，才会从网易刷新所选歌曲并写入`--source`指定的新JSON；脚本会从受支持的网易音频标签读取歌曲ID，也可用`--netease-song-id <数字ID>`覆盖。不带刷新参数时不会请求在线歌词。`karaoke_netease_metadata.py <音频> --identity --fetch-album`会在显式要求时查询专辑详情，补充专辑作者与专辑规模，并与曲目歌手分开保存。专辑显示信息默认读取音频标签，缺失时回退到歌曲名和歌手。变调或无标签的交付音频可用`--metadata-source-audio`指定原始带标签音频。

缺少冻结JSON时，可改用`--lyrics-file <lyrics.lrc|lyrics.txt>`。LRC时间戳会原样保留；UTF-8纯文本按非空行生成歌词行，并在声学对齐前按音频时长生成均匀粗时间锚点，这类时间轴需要后续复核。

现有的`scripts/run_karaoke_japanese_workflow.py`用途不同：它直接重新渲染已有的调整后或复核后的SUG，不运行MSST或MMS。底层的`scripts/run_karaoke_japanese_mms_workflow.py`用于分阶段审计、恢复和门禁检查。

## 运行时与模型边界

从StrangeUtaGame项目根目录使用项目现有的`.venv`：

```powershell
Set-Location <StrangeUtaGame>
uv run --no-sync python --version
```

生产流程跟随Bootstrap的硬件探测，默认使用`--device auto`。需要固定后端时显式覆盖为`--device cuda`或`--device cpu`。生产命令使用项目自有的`models/mms/model.pt`和`models/whisper`，不会隐式下载模型。缺少输入时会直接失败，请单独准备环境。

`local-mms-fa`仍是默认对齐后端。实验性、仅限日文的`NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn`只能通过`--mms-backend nextfire-ja-latn`显式选择，不宣称优于默认后端。它只读取固定本地快照`models/hf/nextfire-mms-ja-latn`，运行时不下载、不使用通用Hugging Face缓存，也不执行远程代码。

环境工具各自承担以下职责：

1. `check_karaoke_environment.py`不会主动发起网络请求。它探测本地命令、目标`.venv`、选定的CUDA/CPU后端和项目自有模型文件。默认只检查模型精确大小；`--deep-verify`才会读取完整模型文件并做SHA-256校验。自定义清单必须加`--allow-custom-manifest`；需要隐藏绝对本地路径时可加`--redact-paths`。
2. `bootstrap_karaoke_environment.py`只有在显式调用时才执行设置。它探测NVIDIA/CPU，复用或创建唯一的`target/.venv`，安装固定版本的Python包，并把缺失的MMS/Whisper文件下载到`target/models/`。自定义清单必须加`--allow-custom-manifest`。MMS模型下载必须加`--accept-mms-cc-by-nc-4-0`，该选项确认必须署名且仅限非商业用途；托管Python下载必须加`--allow-python-download`。
3. Bootstrap不管理`git`、`uv`、`ffmpeg`、`ffprobe`或GPU驱动。默认请把配套的FFmpeg 8.x与FFprobe 8.x安装到`<StrangeUtaGame>/tools/ffmpeg/bin`；具体步骤见[集成说明](references/strangeutagame-integration.zh-CN.md#ffmpeg与ffprobe)。9.x属于显式兼容性迁移，不是默认版本。`--dry-run`会深度校验并规划，但不会写入或主动发起网络请求；`--offline`会阻止模型和Python下载，并把uv置于离线模式。

检查已有目标，不下载也不修改目标：

```powershell
python scripts/check_karaoke_environment.py --target <StrangeUtaGame>
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --deep-verify
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --nextfire-mms-ja-latn
```

需要设置环境时，先查看计划，再显式执行Bootstrap：

```powershell
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --dry-run
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> `
  --accept-mms-cc-by-nc-4-0
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> `
  --nextfire-mms-ja-latn --dry-run
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> `
  --nextfire-mms-ja-latn --accept-nextfire-agpl-3-0 `
  --accept-mms-cc-by-nc-4-0
```

可选NextFire安装必须同时传入两项许可确认。权重只保存在本地，不提交到本仓库；许可摘要见MMS工作流和第三方组件说明。

当所需包和模型都已在本地时，可在显式Bootstrap命令上使用`--offline`。使用自定义清单时追加`--allow-custom-manifest`；需要下载托管Python时追加`--allow-python-download`。当`nvidia-smi`探测到NVIDIA硬件时，Bootstrap清单选择CUDA取向的Torch包，否则选择官方CPU索引。这种选择不会安装驱动。生产流程默认使用`--device auto`，需要固定时显式写`--device cuda`或`--device cpu`。

## 安装集成

从上游仓库获取StrangeUtaGame：
[karaoke-studio/StrangeUtaGame](https://github.com/karaoke-studio/StrangeUtaGame)。
请在本Skill仓库之外单独准备兼容的StrangeUtaGame工作区。

将Skill克隆到Codex技能目录：

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git `
  "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

把集成安装到兼容的StrangeUtaGame工作区。允许替换前查看兼容性结果和dry run：

```powershell
python scripts/install_strangeutagame_integration.py --target <project> --dry-run
python scripts/install_strangeutagame_integration.py --target <project> --force
```

安装器只复制[`dependency-manifest.json`](integration/strangeutagame/dependency-manifest.json)授权的路径，并为被替换文件保留回滚备份。

## StrangeUtaGame依赖

制作流程会使用StrangeUtaGame的SUG领域模型、解析器、导出器以及编辑器和音频接口：

- `karaoke_timing.py`、`render_karaoke_track.py`、`sug_ruby.py`和`karaoke_mms_editable.py`直接导入StrangeUtaGame Python模块。
- Full-auto、分阶段MMS、直接重渲染和批量入口会间接使用这些模块，因此必须从目标工作区通过其现有`.venv`运行。
- 媒体检查、美术图、取色、变调、打包、快照和转码工具不导入应用代码，但其中一部分仍会读取目标项目的清单、SUG、字体、媒体或目录约定。
- 安装器和环境工具通过`--target`接收StrangeUtaGame工作区，并验证所需目录结构。

工作区依赖与安装说明见[StrangeUtaGame集成说明](references/strangeutagame-integration.zh-CN.md)，机器可读的安装清单是[`dependency-manifest.json`](integration/strangeutagame/dependency-manifest.json)。

## 主要命令

从清单歌曲执行日文full-auto制作：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py `
  --manifest <manifest> `
  --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --output-dir <new-output-dir> `
  --quality-policy auto-fallback
```

手工歌词文件可将`--source <frozen-lyrics.json>`替换为`--lyrics-file <lyrics.lrc|lyrics.txt>`。

需要显式试用实验性日文后端时，加入`--mms-backend nextfire-ja-latn`。同一套双音轨审核以及`auto-fallback`/`strict`质量策略仍然适用。

执行日文分阶段MMS/恢复：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> `
  --mms-model-path models/mms/model.pt `
  --quality-policy auto-fallback --output-dir <new-output-dir> `
  --visual-style spectrum
```

需要该实验性选项时，此处同样加入`--mms-backend nextfire-ja-latn`；不要与`--mms-model-path`一起使用。

分阶段入口还接受可选的`--source`、`--sug`和`--vocals-root`覆盖参数。生产过程中不会下载缺失的MMS检查点。

Full-auto、分阶段MMS和已有SUG重渲染命令都接受`--output-mode subtitle-overlay`。省略`--background-video`时输出无音频、带透明通道的ProRes 4444 MOV，供剪辑软件合成。提供`--background-video <视频素材>`时由FFmpeg直接生成常规AV1/AAC MP4：较长素材裁至歌曲区间，较短素材的剩余区间显示黑幕。编码会依次探测`av1_nvenc`和`libaom-av1`；NVENC不可用或渲染失败时自动改用软件AV1编码器。

从已有调整后SUG重新渲染日文视频：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <adjusted-project.sug> --audio <post-mix-audio> `
  --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --visual-style spectrum
```

专辑名称和作者默认读取音频标签；`--album-title`和`--album-artist`只用于显式覆盖。

从已复核时间轴批量渲染AV1 4:2:0：

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <manifest> --visual-style spectrum
```

每次full-auto或分阶段运行都使用新的输出目录。使用`--device auto`跟随Bootstrap探测，也可以显式传入`--device cuda`或`--device cpu`固定后端。每个命令的`--help`输出是参数的最终依据。注音验证仍是可选项：日文分阶段、直接渲染和批量CLI提供`--pronunciation-validation {off,optional,required}`，默认是`optional`；full-auto不要求这个sidecar。

## 布局与交付

full-auto入口会自动准备当前宽屏布局。柱状频谱使用`spectrum`，折线轮廓及其向零幅值基线闭合的填充区域使用`spectrum-line`，黑胶视觉使用`vinyl`。几何参数只在单一事实源[宽屏视觉模板](references/wide-visual-templates.zh-CN.md)中维护。

存在标准交付封面或音频内嵌封面时，美术图会自动生成。需要显式图片时使用`--cover`；构图、背景和封面源音频覆盖项仍然是可选参数。

默认交付物是包含AV1视频、硬字幕和AAC-LC音频的MP4。其他容器和完整解码诊断都必须显式选择，并在提升为交付物前完成验证。详见[批量发布门禁](references/batch-release-gates.zh-CN.md)和[AV1 4:2:0命令](references/av1-420-commands.zh-CN.md)。

## 仓库结构

- `SKILL.md`：精简的入口选择和发布契约。
- `references/`：详细工作流、时间轴、集成和媒体说明。
- `integration/strangeutagame/`：可安装的日文和通用支持文件。
- `scripts/`：安装器、环境检查和显式Bootstrap工具。
- `tests/`：仓库及集成回归测试，不会安装到StrangeUtaGame。
- `ruff.toml`：本仓库的Ruff代码检查配置；它不会创建Python环境，也不会影响生产渲染。

## 文档索引

| 主题 | English | 简体中文 |
| --- | --- | --- |
| Full-auto与MMS | [English](references/mms-workflows.md) | [中文](references/mms-workflows.zh-CN.md) |
| StrangeUtaGame集成与逐脚本依赖 | [English](references/strangeutagame-integration.md) | [中文](references/strangeutagame-integration.zh-CN.md) |
| ASR、SUG与变调 | [English](references/asr-sug-pitch.md) | [中文](references/asr-sug-pitch.zh-CN.md) |
| 宽屏视觉模板 | [English](references/wide-visual-templates.md) | [中文](references/wide-visual-templates.zh-CN.md) |
| 字幕与时间轴质量 | [English](references/subtitle-timing-quality.md) | [中文](references/subtitle-timing-quality.zh-CN.md) |
| AV1 4:2:0命令 | [English](references/av1-420-commands.md) | [中文](references/av1-420-commands.zh-CN.md) |
| 批量发布门禁 | [English](references/batch-release-gates.md) | [中文](references/batch-release-gates.zh-CN.md) |
| 歌手颜色与顶部字幕 | [English](references/singer-overlays.md) | [中文](references/singer-overlays.zh-CN.md) |
| 第三方组件说明 | [English](THIRD_PARTY_NOTICES.md) | [中文](THIRD_PARTY_NOTICES.zh-CN.md) |

## 验证

Skill仓库不会创建第二个项目环境。复用目标工作区的`.venv`执行仓库检查：

```powershell
$project = (Resolve-Path <StrangeUtaGame>).Path
uv run --no-sync --project $project python -m pytest -q `
  --basetemp .test-tmp tests
uv run --no-sync --project $project ruff check --config ruff.toml `
  integration/strangeutagame/scripts scripts tests
uv run --no-sync --project $project python scripts/install_strangeutagame_integration.py `
  --target <project> --dry-run
```

代码和文档使用GPL-3.0-only。运行时组件说明见[第三方组件说明](THIRD_PARTY_NOTICES.zh-CN.md)。
