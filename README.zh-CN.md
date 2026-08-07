# 卡拉 OK AV1 视频制作 Skill

[English](README.md)

本仓库提供可复用的 Codex Skill，以及受保护的 StrangeUtaGame 日文卡拉 OK 时间轴和
AV1 视频制作集成。公开集成只包含日文与通用流程文件；逐曲数据放在外部清单和冻结
歌词源中。

## 可以自动完成什么

推荐的日文入口是单命令
`scripts/run_karaoke_japanese_full_auto.py`。给定清单、歌曲 ID、冻结歌词源和
新的输出目录后，它会自动完成：

- 准备选中歌曲的 MSST 人声分轨；
- 生成私有初始 SUG；
- 运行日文 MMS 流程并生成可编辑的 companion SUG；
- 准备当前布局并渲染 AV1 MP4 交付物。

默认质量策略是 `auto-fallback`。流程可以在没有人工校轴的情况下完成：采用可用的
高置信度 MMS 时间，同时让低置信度或未解决单元保留规范时间，并在报告中保留相应
证据。人工或 Agent 校轴只是针对 companion SUG 的可选后续，不是自动流程的前置条件。

现有的 `scripts/run_karaoke_japanese_workflow.py` 用途不同：它接收已有的人工调整或
复核后的 SUG，直接重新渲染视频；它不会生成私有初始 SUG，也不会运行 MMS。

底层的 `scripts/run_karaoke_japanese_mms_workflow.py` 是分阶段的 MMS/恢复入口。需要
逐阶段处理审计、构建、companion 或渲染，或检查阶段产物时使用它；新日文歌曲的通常
首选命令仍是 full-auto 入口。

“自动”不代表不需要输入。所选歌曲仍需已授权的本地音频、清单、冻结歌词、字体，以及
项目自有的模型或分轨输入。流程不会用未经复核的转写替换冻结歌词。

## 自动布局与交付物

full-auto 流程会自动准备当前宽屏布局。需要频谱呈现时使用默认的 `spectrum`，需要
黑胶视觉时选择 `vinyl`；本次运行生成的布局资源会与该次产物放在一起。具体几何参数
请阅读 [wide-visual-templates.md](references/wide-visual-templates.md)。

默认交付物是包含 AV1 视频、硬字幕和 AAC-LC 音频的 MP4。其他容器或诊断操作需要显式
选择，并在交付前完成验证。

## 安装

将 Skill 克隆到 Codex 技能目录：

```powershell
git clone https://github.com/Kisaragi-Mio-0127/karaoke-av1-video-production-skill.git `
  "$env:USERPROFILE\.codex\skills\karaoke-av1-video-production"
```

把集成安装到已有 StrangeUtaGame 工作区。允许替换前先检查 dry run：

```powershell
python scripts/install_strangeutagame_integration.py --target <project> --dry-run
python scripts/install_strangeutagame_integration.py --target <project> --force
```

安装器只复制
[`dependency-manifest.json`](integration/strangeutagame/dependency-manifest.json)
授权的文件，并为被替换文件保留回滚备份。

## 主要命令

从清单歌曲执行日文 full-auto 制作：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py `
  --manifest <manifest> `
  --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --output-dir <new-private-output-dir> `
  --quality-policy auto-fallback
```

从已有调整后 SUG 重新渲染日文视频：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <adjusted-project.sug> --audio <post-mix-audio> `
  --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style spectrum
```

从已复核时间轴批量渲染 AV1 4:2:0：

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <manifest> --visual-style <vinyl-or-spectrum>
```

每次一键运行都使用新的输出目录。full-auto 默认使用项目配置中的模型和分轨位置；
项目有特殊配置时可以提供显式覆盖。

## 仓库结构

- `SKILL.md`：精简的流程选择和发布契约。
- `references/`：详细工作流、时间轴、集成和媒体说明。
- `integration/strangeutagame/`：可安装的通用及日文支持文件。
- `scripts/`：安装器和本地 Skill 支持工具。
- `tests/`：仓库及集成回归测试，不会安装到 StrangeUtaGame。

## 验证

复用已有 StrangeUtaGame uv 环境；Skill 仓库不会创建第二个虚拟环境：

```powershell
$project = (Resolve-Path <StrangeUtaGame>).Path
uv run --no-sync --project $project python -m pytest -q `
  --basetemp .test-tmp tests
uv run --no-sync --project $project ruff check --config ruff.toml `
  integration/strangeutagame/scripts scripts tests
uv run --no-sync --project $project python scripts/install_strangeutagame_integration.py `
  --target <project> --dry-run
```

代码和文档使用 GPL-3.0-only。运行时组件说明见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
