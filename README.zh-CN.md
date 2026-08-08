# 卡拉OK AV1视频制作Skill

[English](README.md)

本仓库提供可复用的Codex Skill，以及受保护的StrangeUtaGame日文卡拉OK时间轴和AV1视频制作集成。公开包仅包含日文与通用流程；中文和英文工作流保留在独立的本地Skill中。逐曲数据仍放在外部清单和冻结歌词源中。

## 可以自动完成什么

推荐的日文入口是单命令
`scripts/run_karaoke_japanese_full_auto.py`。给定清单、歌曲ID、冻结歌词和新的输出目录后，它会：

- 准备选中歌曲的MSST人声分轨；
- 生成私有初始SUG；
- 运行日文MMS并生成可编辑的companion SUG；
- 准备当前布局并渲染AV1 MP4交付物。

默认质量策略是`auto-fallback`。流程采用可用的高置信度MMS时间，同时让低置信度或未解决单元保留规范时间，并在报告中保留证据。companion SUG生成后，人工或Agent校轴是可选后续，不是自动流程的前置条件。

现有的`scripts/run_karaoke_japanese_workflow.py`用途不同：它直接重新渲染已有的调整后或复核后的SUG，不运行MSST或MMS。底层的`scripts/run_karaoke_japanese_mms_workflow.py`用于分阶段审计、恢复和门禁检查。

## 运行时与模型边界

从StrangeUtaGame项目根目录使用项目现有的`.venv`：

```powershell
Set-Location <StrangeUtaGame>
uv run --no-sync python --version
```

公开运行时跟随Bootstrap的硬件探测，默认使用`--device auto`。需要固定后端时显式覆盖为`--device cuda`或`--device cpu`。生产命令使用项目自有的`models/mms/model.pt`和`models/whisper`，不会隐式下载模型。缺少输入时会直接失败，请单独准备环境。

公开环境工具的边界不同：

1. `check_karaoke_environment.py`不会主动发起网络请求。它探测本地命令、目标`.venv`、选定的CUDA/CPU后端和项目自有模型文件。默认只检查模型精确大小；`--deep-verify`才会读取完整模型文件并做SHA-256校验。自定义清单必须加`--allow-custom-manifest`；需要隐藏绝对本地路径时可加`--redact-paths`。
2. `bootstrap_karaoke_environment.py`只有在显式调用时才执行设置。它探测NVIDIA/CPU，复用或创建唯一的`target/.venv`，安装固定版本的Python包，并把缺失的MMS/Whisper文件下载到`target/models/`。自定义清单必须加`--allow-custom-manifest`。MMS模型下载必须加`--accept-mms-cc-by-nc-4-0`，该选项确认必须署名且仅限非商业用途；托管Python下载必须加`--allow-python-download`。
3. Bootstrap不管理`git`、`uv`、`ffmpeg`、`ffprobe`或GPU驱动，这些必须单独安装和维护。`--dry-run`会深度校验并规划，但不会写入或主动发起网络请求；`--offline`会阻止模型和Python下载，并把uv置于离线模式。

检查已有目标，不下载也不修改目标：

```powershell
python scripts/check_karaoke_environment.py --target <StrangeUtaGame>
python scripts/check_karaoke_environment.py --target <StrangeUtaGame> --deep-verify
```

需要设置环境时，先查看计划，再显式执行Bootstrap：

```powershell
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> --dry-run
python scripts/bootstrap_karaoke_environment.py --target <StrangeUtaGame> `
  --accept-mms-cc-by-nc-4-0
```

当所需包和模型都已在本地时，可在显式Bootstrap命令上使用`--offline`。使用自定义清单时追加`--allow-custom-manifest`；需要下载托管Python时追加`--allow-python-download`。当`nvidia-smi`探测到NVIDIA硬件时，Bootstrap清单选择CUDA取向的Torch包，否则选择官方CPU索引。这种选择不会安装驱动。公开生产运行时仍默认使用`--device auto`，需要固定时显式写`--device cuda`或`--device cpu`。

## 安装集成

将Skill克隆到Codex技能目录：

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git `
  "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

把集成安装到已有的StrangeUtaGame工作区。允许替换前先检查dry run：

```powershell
python scripts/install_strangeutagame_integration.py --target <project> --dry-run
python scripts/install_strangeutagame_integration.py --target <project> --force
```

安装器只复制
[`dependency-manifest.json`](integration/strangeutagame/dependency-manifest.json)授权的路径，并为被替换文件保留回滚备份。

## 主要命令

从清单歌曲执行日文full-auto制作：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py `
  --manifest <manifest> `
  --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --output-dir <new-private-output-dir> `
  --quality-policy auto-fallback
```

执行日文分阶段MMS/恢复：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> `
  --mms-model-path models/mms/model.pt `
  --quality-policy auto-fallback --output-dir <new-private-output-dir> `
  --visual-style spectrum
```

分阶段入口还接受可选的`--source`、`--sug`和`--vocals-root`覆盖参数。生产过程中不会下载缺失的MMS检查点。

从已有调整后SUG重新渲染日文视频：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <adjusted-project.sug> --audio <post-mix-audio> `
  --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style spectrum
```

从已复核时间轴批量渲染AV1 4:2:0：

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <manifest> --visual-style spectrum
```

每次full-auto或分阶段运行都使用新的输出目录。公开运行时使用`--device auto`跟随Bootstrap探测，也可以显式传入`--device cuda`或`--device cpu`固定后端。每个命令的`--help`输出是参数的最终依据。注音验证仍是可选项：日文分阶段、直接渲染和批量CLI提供`--pronunciation-validation {off,optional,required}`，默认是`optional`；full-auto不要求这个sidecar。

## 布局与交付

full-auto入口会自动准备当前宽屏布局。默认频谱呈现使用`spectrum`，黑胶视觉使用`vinyl`。几何参数只在单一事实源
[wide-visual-templates.md](references/wide-visual-templates.md)中维护。

默认交付物是包含AV1视频、硬字幕和AAC-LC音频的MP4。其他容器和完整解码诊断都必须显式选择，并在提升为交付物前完成验证。详见
[batch-release-gates.md](references/batch-release-gates.md)和
[av1-420-commands.md](references/av1-420-commands.md)。

## 仓库结构

- `SKILL.md`：精简的入口选择和发布契约。
- `references/`：详细工作流、时间轴、集成和媒体说明。
- `integration/strangeutagame/`：可安装的日文和通用支持文件。
- `scripts/`：安装器、环境检查和显式Bootstrap工具。
- `tests/`：仓库及集成回归测试，不会安装到StrangeUtaGame。

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

代码和文档使用GPL-3.0-only。运行时组件说明见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
