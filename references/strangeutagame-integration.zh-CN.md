# StrangeUtaGame集成

[English](strangeutagame-integration.md) | 简体中文

本集成只能用于已获授权的StrangeUtaGame工作树。仓库中的文件是脱敏流水线快照，不是完整GUI应用，也不是从上游Git历史复制的文件。直接依赖、传递依赖、成品目录依赖和不导入上游代码的分类见README与`integration/strangeutagame/dependency-manifest.json`。

## 安装与环境

先预览，再安装：

```powershell
python scripts/install_strangeutagame_integration.py --target D:\path\to\StrangeUtaGame --dry-run
python scripts/install_strangeutagame_integration.py --target D:\path\to\StrangeUtaGame
```

安装器检查`pyproject.toml`、`src/strange_uta_game`和`scripts`；不同文件默认拒绝覆盖，使用`--force`前必须人工审查，旧文件会先备份到`.karaoke-skill-backup/<UTC stamp>/`。

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
python scripts/check_karaoke_environment.py --target D:\path\to\StrangeUtaGame
```

当前已验证应用基线是StrangeUtaGame 1.4.5，SUG存储格式是0.3.0。应用版本应从`src/strange_uta_game/__version__.py`确认，格式应从`SugMigrator.CURRENT_VERSION`确认；`pyproject.toml`中的1.2.6不是版本真源。升级后用`scripts/check_sug_compatibility.py`检查代表性项目，并要求前后哈希不变。文档化的ASR/对齐流程使用已配置的语言 profile。

## 私有数据与脚本流程

复制`examples/album.example.json`到私有目录并替换全部占位信息，通过`--manifest`或`KARAOKE_ALBUM_MANIFEST`传入。真实清单、歌词、哈希、时间覆盖、字体、报告和媒体不得提交到本仓库。网络默认关闭；获取歌词源要显式使用`--refresh-source`，获取公开封面要显式使用`--allow-network`，并遵守HTTPS、公共地址、不重定向和25 MiB限制。

脚本流程为：

```text
清单 -> 可选MSST证据 -> ASR/MMS审计 -> 源歌词
-> 候选注音填入规范SUG -> Agent按整句上下文审核/回写
-> 时间/短语决定 -> 只读renderer -> ASS/报告/最终帧 -> 构图
-> HEVC/AV1渲染 -> 媒体检查 -> 最终化 -> 归档/快照
```

本仓库的支持工具包括用于只读SUG验证的`scripts/check_sug_compatibility.py`，以及用于处理完整混音变调的`scripts/pitch_shift_audio.py`。后者也会随生产脚本快照安装；验证后的FLAC和JSON报告必须作为一组保留。

歌曲专用显示和注音决定只放在私有JSON中，用于已批准例外或歧义、专名、艺术读音、证据冲突、低置信、`unresolved`升级；保护已有人工或legacy注音。候选生成器只填补缺失注音并先写入规范SUG，Agent按整句歌词、语法、词形、词边界和上下文自动审核每条注音，可批准或直接回写修正；无修改时沿用默认注音。renderer只读审核后的规范SUG，渲染阶段禁止再推断或覆盖。每个span记录状态、置信度、evidence、model/prompt版本和SUG修改前后哈希，SUG、ASS/报告和最终帧必须一致。
人工调轴默认跳过；确需人工检查时，先用编辑器音频探针确认真实项目和音频加载，再以正常可编辑模式打开同一规范SUG。最终可编辑时间轴源是`.sug`，探针JSON只是证据。独立ASR不可用或失败时记录`unresolved`，不能用插值或强制对齐冒充；使用已配置的语言profile，并保持源文本不变（经过审查的profile专用规范化除外）。

请求升降调时，在时间轴和渲染之前对完整混音运行`scripts/pitch_shift_audio.py`。验证后的FLAC同时供时间证据、默认MP4的AAC-LC 320 kb/s和配对MKV的FLAC使用；不能从MP4 AAC制作无损音轨。

运行时报告可能包含本地路径、媒体哈希、模型路径和源URL，应放在被忽略的私有目录并在分享前脱敏。不要提交探针、审计、验证或快照JSON。
