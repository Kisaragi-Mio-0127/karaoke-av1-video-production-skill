[简体中文](README.zh-CN.md) | [English](README.md)

# 卡拉 OK AV1 视频制作 Skill

这是一个面向 Codex 的卡拉 OK 视频制作 Skill，同时包含经过脱敏的
StrangeUtaGame 集成脚本。它覆盖可编辑时间轴、字幕审核、渲染、媒体验证和
AV1 4:2:0 成品打包流程。

## 包含内容

- `SKILL.md` 中的完整制作流程，以及字幕时间轴和发布门禁参考文档。
- 19 个经过脱敏的 StrangeUtaGame 生产脚本，覆盖时间轴、日语注音、
  ASR/MMS 证据、封面与黑胶画面、HEVC/AV1 渲染、媒体检查、最终发布和归档。
- 带保护的一键安装器：先检查目标仓库，拒绝符号链接、目录冲突和未授权覆盖；
  强制更新时先备份，并在中途失败时回滚。
- 用于人工调轴前验证项目和音轨加载状态的编辑器探针。
- Windows 可复现依赖锁、环境检查器、通用专辑清单和私有 override 示例。

仓库不包含真实录音、歌词、专辑信息、字体、封面、模型、API 凭据、渲染视频
或真实项目报告。

## 安装 Codex Skill

Windows PowerShell：

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

该仓库为公开仓库，克隆和阅读不需要 GitHub 身份验证；只有向自己拥有写入权限的
仓库推送修改时才需要登录。

在 Codex 中显式调用：

```text
$karaoke-av1-video-production
```

## 安装 StrangeUtaGame 生产脚本

这些集成脚本依赖完整的 StrangeUtaGame 应用代码，不能代替应用本身。

先预览安装计划，不写入文件：

```powershell
python scripts/install_strangeutagame_integration.py --target D:\path\to\StrangeUtaGame --dry-run
```

确认 JSON 计划后执行安装：

```powershell
python scripts/install_strangeutagame_integration.py --target D:\path\to\StrangeUtaGame
```

若目标脚本与集成版本不同，安装器默认拒绝覆盖。只有人工检查后才能使用
`--force`；旧文件会先保存到 `.karaoke-skill-backup/<UTC 时间与运行 ID>/`。

## 配置 Python 环境

Python 3.12 是测试基线，脚本最低要求 Python 3.10。推荐使用项目本地的
[`uv`](https://docs.astral.sh/uv/getting-started/installation/) 环境。

```powershell
Set-Location D:\path\to\StrangeUtaGame
winget install --id=astral-sh.uv -e
uv python install 3.12
uv venv --python 3.12
uv pip install -r requirements-karaoke.skill.lock.txt
```

还需要单独安装 `ffmpeg` 和 `ffprobe`，并自行提供具有合法使用权的 CJK 字体。
Rubber Band 仅在升降调时需要；Whisper/MMS 和外部 MSST 属于可选证据流程。

完整环境说明、官方链接、脚本用途和专辑清单格式请参阅
[`references/strangeutagame-integration.md`](references/strangeutagame-integration.md)。

运行环境检查：

```powershell
python scripts/check_karaoke_environment.py --target D:\path\to\StrangeUtaGame
```

## 脚本来源与依赖边界

仓库中的 19 个生产脚本都是后来为这套制作流程编写的集成脚本。它们在脱敏打包前是
生产工作树中的未跟踪新增文件，并不是从 StrangeUtaGame 上游 Git 历史中取出的原有
脚本。下表中的“直接依赖”表示导入了另行获取的 StrangeUtaGame 应用中由 Git 正式
跟踪的模块；“传递依赖”表示导入或执行了会加载这些上游模块的其他集成脚本。

| 脚本 | 依赖边界 | 具体依赖或用途 |
|---|---|---|
| `karaoke_timing.py` | 直接依赖上游模块 | 导入领域实体、导出器和 `SugProjectParser`。 |
| `karaoke_review_preview.py` | 直接依赖上游模块 | 导入 `Character`、`Sentence` 和 `SugProjectParser`。 |
| `convert_english_sug_word_tokens.py` | 直接依赖上游模块 | 导入 `SugProjectParser` 和 SUG 时间轴转换逻辑。 |
| `sync_karaoke_editable_ruby.py` | 传递运行依赖 | 从上述 StrangeUtaGame 相关脚本导入上下文注音与专辑时间轴数据。 |
| `audit_karaoke_asr_recognition.py` | 传递运行依赖 | 从 `karaoke_timing.py` 导入 LRC 工具；加载该模块时会加载 StrangeUtaGame。 |
| `audit_karaoke_mms_alignment.py` | 传递运行依赖 | 导入 `karaoke_timing.py`，并读取 SUG JSON 时间轴证据。 |
| `render_karaoke_direct_av1_album.py` | 传递运行依赖 | 使用 SUG 输入执行 `karaoke_review_preview.py`，重新生成 ASS。 |
| `render_karaoke_direct_hevc444_album.py` | 传递运行依赖 | 委托给直接 AV1 渲染器，因此沿用其 SUG 预览流程。 |
| `render_karaoke_direct_av1_420_album.py` | 传递运行依赖 | 导入注音同步器并执行 SUG 预览渲染器。 |
| `finalize_karaoke_release.py` | SUG 成品/目录依赖 | 不导入应用代码，但会检查 `.sug` 文件和集成发布目录。 |
| `karaoke_album.py` | 不导入上游代码 | 定义脱敏专辑清单和路径模型。 |
| `karaoke_language.py` | 不导入上游代码 | 提供语言规范化和分词工具。 |
| `build_karaoke_wide_artwork.py` | 不导入上游代码 | 使用 Pillow 生成画面素材。 |
| `render_vinyl_karaoke.py` | 不导入上游代码 | 使用媒体与图像库生成黑胶画面层。 |
| `inspect_karaoke_media.py` | 不导入上游代码 | 检查编码媒体和共享渲染元数据。 |
| `transcode_karaoke_av1.py` | 不导入上游代码 | 使用 FFmpeg 元数据转码并验证媒体。 |
| `prepare_karaoke_msst_vocals.py` | 不导入上游代码 | 为用户另行提供的 MSST 程序准备可选证据。 |
| `package_karaoke_numbered_archives.py` | 不导入上游代码 | 根据清单路径生成编号归档。 |
| `karaoke_release_snapshot.py` | 不导入上游代码 | 创建和恢复发布文件快照。 |

辅助工具另行分类：`open_editable_project_with_audio_probe.py` 会动态导入应用的 GUI、
持久化和音频加载模块；`install_strangeutagame_integration.py` 与
`check_karaoke_environment.py` 不导入应用代码，但会明确操作现有的 StrangeUtaGame
工作树。机器可读的权威清单位于
[`integration/strangeutagame/dependency-manifest.json`](integration/strangeutagame/dependency-manifest.json)。

## 私有项目数据

复制 `examples/album.example.json` 到私有项目目录，替换全部占位信息，然后通过
`--manifest` 或环境变量传入：

```powershell
$env:KARAOKE_ALBUM_MANIFEST = "D:\private\album.json"
uv run python scripts/karaoke_timing.py --manifest $env:KARAOKE_ALBUM_MANIFEST --allow-partial-manifest
```

歌曲专用的字幕分句、ruby 分组和上下文读音应保存在私有 JSON 中：

- `KARAOKE_DISPLAY_OVERRIDES`
- `KARAOKE_RUBY_GROUP_OVERRIDES`
- `KARAOKE_TIMING_READING_OVERRIDES`

网络访问默认关闭。只有显式传入 `--refresh-source` 才会获取歌词源；只有显式传入
`--allow-network` 才允许在没有内嵌封面时获取公开 HTTPS 封面。

## 仓库结构

```text
.
├── SKILL.md
├── LICENSE
├── NOTICE.md
├── THIRD_PARTY_NOTICES.md
├── agents/
├── examples/
├── integration/strangeutagame/
│   ├── dependency-manifest.json # 来源与依赖边界
│   ├── requirements/
│   └── scripts/                 # 19 个脱敏生产脚本
├── references/
├── scripts/
│   ├── check_karaoke_environment.py
│   ├── install_strangeutagame_integration.py
│   └── open_editable_project_with_audio_probe.py
└── tests/
```

## 测试

```powershell
python -m unittest discover -s scripts -p "test_*.py" -v
python -m unittest discover -s tests -p "test_*.py" -v
```

目前包含 21 项单元测试，覆盖：

- 所有集成脚本的语法解析和直接入口导入方式回归。
- 通用专辑清单和私有 override 示例格式。
- 安装冲突、备份、符号链接/重解析点防护和中途失败回滚。
- 网络必须显式启用，以及不安全 URL、重定向和超大封面的拒绝逻辑。
- 可执行脚本中的机器路径、凭据形式、专辑专用默认路径和固定网络封面扫描。

这些是打包和安全测试，不代表在没有私有媒体的情况下完成过整首视频渲染。
正式制作前仍须运行环境检查、19 个命令的 `--help` 烟测、短预览和
`SKILL.md` 中的发布门禁。

## 许可证与素材权利

代码使用 GPL-3.0-only，具体说明见 `LICENSE`、`NOTICE.md` 和
`THIRD_PARTY_NOTICES.md`。

本仓库是后来为
[karaoke-studio/StrangeUtaGame](https://github.com/karaoke-studio/StrangeUtaGame)
编写的集成项目；该上游仓库声明使用 GPL-3.0。本仓库不是上游应用源码副本，也不重新
分发该应用。集成开发基于 StrangeUtaGame 1.2.6、提交
`d1b121a53c8b9167986933c21afa1d1c9d8a0355`，详细来源与依赖边界见
`NOTICE.md` 和 `integration/strangeutagame/dependency-manifest.json`。

使用者必须自行确认录音、歌词展示与同步、封面、字体、模型和最终成品分发权。
FFmpeg 的实际许可证取决于具体构建选项，发布前应检查所使用构建的配置。
