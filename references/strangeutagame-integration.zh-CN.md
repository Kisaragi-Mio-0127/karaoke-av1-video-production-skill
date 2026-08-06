# StrangeUtaGame集成

[English](strangeutagame-integration.md) | 简体中文

本集成只能用于已获授权的StrangeUtaGame工作树。仓库中的文件是脱敏流水线快照，不是完整GUI应用，也不是从上游Git历史复制的文件。直接依赖、传递依赖、成品目录依赖和不导入上游代码的分类见README与`integration/strangeutagame/dependency-manifest.json`。

## 安装与环境

先预览，再安装：

```powershell
$skillRoot = (Resolve-Path .).Path
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target D:\path\to\StrangeUtaGame --dry-run
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target D:\path\to\StrangeUtaGame
```

安装器检查`pyproject.toml`、`src/strange_uta_game`和`scripts`；不同文件默认拒绝覆盖，使用`--force`前必须人工审查，旧文件会先备份到`.karaoke-skill-backup/<UTC stamp>/`。Python文件不再按顶层glob复制，而是严格读取清单中的`scripts`、`shared_modules`和递归`package_files`；公开日文/通用说明涉及的包为`karaoke_common/`与`karaoke_japanese/`，包目录会连同父目录一起安装，避免文档已更新但import失败。

Python 3.12是测试基线，公开脚本要求3.10或更新版本。使用项目本地`uv`：

```powershell
winget install --id=astral-sh.uv -e
Set-Location D:\path\to\StrangeUtaGame
uv python install 3.12
uv venv --python 3.12
uv pip install -r requirements-karaoke.skill.lock.txt
```

另行安装`ffmpeg`/`ffprobe`并确认字幕/libass、`av1_nvenc`或`libaom-av1`；Rubber Band只在变调时需要。CJK字体、Whisper/MMS/MSST模型及其许可证由使用者另行提供。安装后运行：

```powershell
Set-Location D:\path\to\karaoke-av1-video-production-skill
python scripts/check_karaoke_environment.py --target D:\path\to\StrangeUtaGame
```

当前要求并验证的版本为StrangeUtaGame 1.4.5和SUG存储格式0.3.0。`src/strange_uta_game/__version__.py`与`pyproject.toml`中的应用版本必须保持一致，SUG格式版本以`SugMigrator.CURRENT_VERSION`为准。升级后从Skill仓库运行`scripts/check_sug_compatibility.py`检查代表性项目，例如：

```powershell
Set-Location D:\path\to\karaoke-av1-video-production-skill
python scripts/check_sug_compatibility.py --repo D:\path\to\StrangeUtaGame --project D:\path\to\representative.sug
```

并要求前后哈希不变。公开工作流使用`scripts/run_karaoke_japanese_workflow.py`；其他语言必须通过单独验证的adapter接入，禁止静默回退。

## 私有数据与脚本流程

复制`examples/album.example.json`到私有目录并替换全部占位信息，通过`--manifest`或`KARAOKE_ALBUM_MANIFEST`传入。真实清单、歌词、哈希、时间覆盖、字体、报告和媒体不得提交到本仓库。网络默认关闭；获取歌词源要显式使用`--refresh-source`，获取公开封面要显式使用`--allow-network`，并遵守HTTPS、公共地址、不重定向和25 MiB限制。

脚本流程为：

```text
清单 -> 日语workflow -> 可选MSST证据 -> ASR/MMS审计 -> 源歌词
-> 候选注音填入规范SUG -> Agent按整句上下文审核/回写
-> 时间/短语决定 -> 只读renderer -> ASS/报告/最终帧 -> 构图
-> HEVC/AV1渲染 -> 媒体检查 -> 最终化 -> 归档/快照
```

本仓库的支持工具包括用于只读SUG验证的`scripts/check_sug_compatibility.py`，以及用于处理完整混音变调的`scripts/pitch_shift_audio.py`。公开日文/通用说明涉及`scripts/karaoke_workflow.py`、`scripts/run_karaoke_japanese_workflow.py`、`karaoke_common/`与`karaoke_japanese/`；生产快照的完整入口与包文件仍由`integration/strangeutagame/dependency-manifest.json`决定。验证后的FLAC和JSON报告必须作为一组保留。

歌曲专用显示和注音决定只放在私有JSON中，用于已批准例外或歧义、专名、艺术读音、证据冲突、低置信、`unresolved`升级；保护已有人工或legacy注音。注音校验暴露`optional`、`required`和`off`，默认`optional`；在`optional`下，缺少pronunciation sidecar只记录为未执行，不得默认阻塞；结构性注音以及SUG、ASS/报告和最终帧的一致性仍是必需门禁。候选生成器只填补缺失注音并先写入规范SUG，Agent按整句歌词、语法、词形、词边界和上下文自动审核每条注音，可批准或直接回写修正；无修改时沿用默认注音。renderer只读审核后的规范SUG，渲染阶段禁止再推断或覆盖。每个存在的span记录状态、置信度、evidence、model/prompt版本和SUG修改前后哈希。
人工调轴默认跳过；确需人工检查时，先用编辑器音频探针确认真实项目和音频加载，再以正常可编辑模式打开同一规范SUG。最终可编辑时间轴源是`.sug`，探针JSON只是证据。独立ASR不可用或失败时记录`unresolved`，不能用插值或强制对齐冒充；使用已配置的语言profile，并保持源文本不变（经过审查的profile专用规范化除外）。

请求升降调时，在时间轴和渲染之前对完整混音运行`scripts/pitch_shift_audio.py`。验证后的FLAC供时间证据和默认MP4的AAC-LC 320 kb/s使用。MKV严格opt-in：只有用户明确传入`--lossless-companion`（或底层`--lossless-output`）且探测源确为FLAC或PCM WAV时才生成；MP3/AAC及其他有损源请求必须拒绝。不能从MP4 AAC制作无损音轨；未请求时不创建、不期待也不报告MKV。

黑胶始终旋转，不把构图层的静态背景误写成日语默认静态。每次正式或测试流程都用`direction-neutral-concentric-grooves/v3/backplate-absent`重新生成黑胶，记录生成器和`vinyl_sha256`，并把准确的新路径显式传给预览/渲染器；规范旧黑胶只保留为身份参照。若封面来自另一份音频，单独记录`cover-source-audio`，不要冒充delivery audio。

黑胶始终旋转。宽屏构图采用`wide-layout-v5/no-right-panels`：黑胶卡为`(40,30,340,402)`，footer底部留白为`12`，底部字幕面板从`y=576`开始。额外叠加的outer right panel（大框）和旋转黑胶后方/下方的compact暗色backplate（小框）都不存在；专辑卡片、卡片footer和底部字幕面板保留。报告应为`right_panel_visible=false`、`outer_right_panel_visible=false`、`vinyl_backplate_present=false`和`vinyl_backplate_preserved=false`。频谱版本不得重新引入原黑胶区域背景框，并使用clip-safe区域`(736,226,1168,348)`、64 px水平辉光余量、上下各56 px辉光余量和上下各8 px柱体安全余量。

兼容性检查器留在Skill仓库，不由安装器复制到目标工作树；只有清单中的生产脚本和递归包文件会被安装。

运行时报告可能包含本地路径、媒体哈希、模型路径和源URL，应放在被忽略的私有目录并在分享前脱敏。不要提交探针、审计、验证或快照JSON。
