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

当前宽屏构图、spectrum/vinyl选择和次级覆盖规则只在
[wide-visual-templates.md](wide-visual-templates.md)中定义。本集成参考只保留高层说明，不复制几何常数。

默认交付是带硬字幕和AAC-LC音频的AV1`yuv420p`MP4。MKV/FLAC和完整空解码都是显式选项。提升交付前执行字幕、流、时长和代表帧门禁；详见
[av1-420-commands.md](av1-420-commands.md)和
[batch-release-gates.md](batch-release-gates.md)。

## 已安装文件与验证

日文/通用包包含`dependency-manifest.json`列出的授权脚本、语言中立共享模块、包文件、依赖和支持工具。公开仓库的测试不会安装到StrangeUtaGame。

使用实际目标运行兼容性和环境检查：

```powershell
Set-Location <StrangeUtaGame>
uv run --no-sync python D:\path\to\skill\scripts/check_sug_compatibility.py --repo . --project <project.sug>
python D:\path\to\skill\scripts/check_karaoke_environment.py --target .
```

如果`ffmpeg`或`ffprobe`缺失，环境检查可能返回非零；这只是诊断结果，不表示生产流程可以下载这些工具。
