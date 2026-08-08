# 日文Full-auto与MMS契约

[English](mms-workflows.md) | 简体中文

从StrangeUtaGame项目根目录使用`uv run --no-sync`运行命令。本公开集成包含日文和语言中立的工作流文件。

## Full-auto首次运行

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_full_auto.py `
  --manifest <manifest> --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --output-dir .render-work/<new-run-dir>
```

该命令解析选定的日文清单曲目，并执行：

```text
preflight -> MSST vocal stem -> private initial SUG -> MMS audit/build
-> editable companion SUG -> automatic current layout -> AV1 MP4
```

输出根目录必须不存在，且必须位于项目的`.render-work`目录下。运行绝不会覆盖清单、冻结歌词源、规范SUG、已接受媒体或模型文件。

命令中展示的四项输入是必需项，美术参数是可选项。`--cover`可显式指定图片；否则流程会优先复用标准交付目录中的`cover.jpg`，不存在时再读取所选封面音频的内嵌图片。`--background`、`--composition`和`--cover-source-audio`是显式的高级覆盖项。

默认值：

- `--quality-policy auto-fallback`
- `--visual-style spectrum`
- MMS检查点`models/mms/model.pt`
- 对齐模型目录`models/whisper`
- 派生的MSST和运行时数据位于`.cache`下

`auto-fallback`会应用可用的MMS时间，并为低置信度或未解析单元保留初始时间。它保留原始证据并报告`rendered-with-fallback`；这不代表质量通过。结构性SUG、字幕、模型和媒体失败仍会停止运行。当不确定性应保留伴侣文件并在渲染前停止时，传入`--quality-policy strict`。

## 分阶段MMS路径

需要恢复或检查时使用较低层的包装器：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> `
  --source <frozen-lyrics.json> `
  --sug <private-or-reviewed.sug> `
  --output-dir <new-private-output-dir> `
  --mms-model-path models/mms/model.pt `
  --quality-policy <strict-or-auto-fallback> `
  --visual-style spectrum
```

不带`--sug`时，包装器解析清单的规范SUG。带`--sug`时，审计和构建来源必须绑定到同一个显式单曲项目。包装器顺序为：

```text
audit -> timing override build -> companion SUG -> release decision -> render
```

在应用质量策略前创建伴侣文件。将它与输入SUG分开保存。如果存在视觉释放覆盖，将sidecar传给渲染；否则使用保留的句子释放来渲染伴侣文件。

## 已有SUG路径

使用`scripts/run_karaoke_japanese_workflow.py`重新渲染已经审核或手动调整的SUG。该路径生成当前布局和视频，但不准备MSST、不构建初始时间，也不调用MMS。

## 模型与缓存边界

- 将模型权重放在`models`下，绝不放在`.cache`中。
- 将MSST解码输入、分离人声、运行时文件及识别/对齐缓存记录放在`.cache`下。
- 不要隐式下载缺失的模型。
- 在报告中记录模型和产物身份，不要使用媒体哈希作为质量或流程门禁。

## 日文注音与发布

在选定SUG中保留已审核注音。纯片假名不接收单独注音，被忽略的过时纯片假名注音不得改变输入。MMS时间不得改写注音或冻结显示文本。

默认交付是带硬字幕和AAC-LC的AV1`yuv420p`MP4。MKV/FLAC和完整空解码仍是显式选项。在每次新运行中生成当前布局，并在提升前执行常规字幕、颜色、流、时长和代表帧门禁。
