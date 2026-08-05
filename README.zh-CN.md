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

该仓库目前是私有仓库，克隆时需要完成 GitHub 身份验证。

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

目前包含 20 项单元测试，覆盖：

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

使用者必须自行确认录音、歌词展示与同步、封面、字体、模型和最终成品分发权。
FFmpeg 的实际许可证取决于具体构建选项，发布前应检查所使用构建的配置。
