# 卡拉 OK AV1 视频制作 Skill

[English](README.md)

本仓库提供可复用的 Codex Skill，以及受保护的 StrangeUtaGame 日文卡拉 OK
时间轴和 AV1 视频制作集成。公开集成只包含通用及日文流程代码；逐曲数据放在
外部清单和复核 JSON 中。

## 可以自动完成什么

- 普通日文入口读取已有 SUG 和音频，自动生成当前构图并渲染最终 MP4。
- 日文 MMS 入口自动审计时间轴、生成独立的可编辑 companion SUG、生成当前
  构图并渲染最终 MP4。需要无人工调轴完成时使用
  `--quality-policy auto-fallback`，未解决证据仍会原样保留在报告中。
- 时间轴构建脚本可先从清单和冻结歌词源生成规范时间轴产物，再交给任一渲染
  入口。

“自动”不代表不需要输入。所选流程仍需本地清单、已授权音频、冻结歌词、字体，
以及对应模型或人声分轨。流程不会用未经复核的转写替换冻结歌词。

## 固定的自动布局

一键流程会在每个新输出目录中生成 `wide-layout-v7/cover-palette`。
`vinyl` 会为本次运行重新生成黑胶资源；`spectrum` 不生成黑胶资源。显式构图
文件仅作为高级兼容覆盖，并且仍须通过同一布局门禁。

具体几何参数只保存在
[wide-visual-templates.zh-CN.md](references/wide-visual-templates.zh-CN.md)。

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

从已有 SUG 生成普通日文视频：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <project.sug> --audio <audio> --output-dir <new-output> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style spectrum
```

无需人工调轴生成日文 MMS companion 和视频：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> `
  --mms-model-path models/mms/model.pt `
  --quality-policy auto-fallback `
  --output-dir <new-output> --visual-style spectrum
```

从已复核时间轴批量渲染 AV1 4:2:0：

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <manifest> --visual-style <vinyl-or-spectrum>
```

默认输出 MP4。只有显式提供相应参数后才生成 MKV 或执行完整空解码。歌词和
封面联网也分别需要显式授权。

## 仓库结构

- `SKILL.md`：精简的流程选择和发布契约。
- `references/`：详细工作流、布局、时间轴和媒体门禁。
- `integration/strangeutagame/`：可安装的通用及日文脚本。
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
