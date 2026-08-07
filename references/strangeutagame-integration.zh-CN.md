# StrangeUtaGame 集成

[English integration reference](strangeutagame-integration.md) | 中文

本文件是中文入口，具体安装、环境和媒体说明请阅读英文集成参考。日文公开流程的
首选入口是：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py `
  --manifest <manifest> `
  --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --output-dir <new-private-output-dir>
```

这条命令会依次准备 MSST、生成私有初始 SUG、运行日文 MMS、生成可编辑 companion、
准备当前布局并渲染 AV1 MP4。默认使用 `auto-fallback`，人工或 Agent 校轴只是生成
companion 后的可选后续。

入口职责保持区分：已有的
`scripts/run_karaoke_japanese_workflow.py` 用于对人工调整或复核后的 SUG 直接重新渲染；
`scripts/run_karaoke_japanese_mms_workflow.py` 用于审计、构建、companion、渲染的分阶段
处理、恢复和门禁检查。

MMS、模型/缓存、SUG 和阶段产物规则见
[MMS workflow contract](mms-workflows.md)。布局只在
[single-source wide-layout contract](wide-visual-templates.md) 中维护，本入口不复制几何
常数。
