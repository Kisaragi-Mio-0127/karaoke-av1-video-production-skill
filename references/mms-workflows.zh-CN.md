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
-> relocatable editable SUG snapshot
```

输出根目录必须不存在，且必须位于项目的`.render-work`目录下。运行绝不会覆盖清单、冻结歌词源、规范SUG、已接受媒体或模型文件。

清单、歌曲ID、新输出目录和一种歌词输入是必需项。冻结JSON或网易刷新目标使用`--source`，手工UTF-8 LRC/TXT使用`--lyrics-file`。美术参数是可选项。`--cover`可显式指定图片；否则流程会优先复用标准交付目录中的`cover.jpg`，不存在时再读取所选封面音频的内嵌图片。`--background`、`--composition`和`--cover-source-audio`是显式的高级覆盖项。

默认沿用冻结歌词源。需要主动从网易刷新当前单曲时，显式加入`--refresh-source`，此时`--source`是刷新后JSON的写入位置；脚本会读取受支持的音频内嵌歌曲ID，也可用`--netease-song-id <数字ID>`覆盖。不带刷新参数时不发起歌词网络请求。

带时间戳的LRC保持原内容。纯文本按非空行生成歌词行，并在对齐前按音频时长生成均匀粗时间锚点；生成时间轴需要后续复核。

专辑显示信息默认读取音频标签，缺失时回退到歌曲名和歌手。变调或无标签的交付音频可用`--metadata-source-audio`指定原始带标签音频。每次成功渲染还会在`render/editable-project`下写出媒体路径已校验的可编辑SUG。

首次运行的歌曲不要求提供`lyric_corrections.json`。未提供校正侧车文件时，MMS审核记录`lyric_corrections_status=not-provided`以及空路径和空哈希，并继续使用冻结歌词源；若提供该文件，其路径仍作为显式审核输入。

默认值：

- `--quality-policy auto-fallback`
- `--visual-style spectrum`
- MMS检查点`models/mms/model.pt`
- 对齐模型目录`models/whisper`
- 派生的MSST和运行时数据位于`.cache`下

`auto-fallback`会应用可用的MMS时间，并为低置信度或未解析单元保留初始时间。它保留原始证据并报告`rendered-with-fallback`；这不代表质量通过。结构性SUG、字幕、模型和媒体失败仍会停止运行。当不确定性应保留伴侣文件并在渲染前停止时，传入`--quality-policy strict`。

## 实验性NextFire日文后端

`local-mms-fa`是默认值。仅在full-auto或分阶段命令中通过`--mms-backend nextfire-ja-latn`显式选择实验性、仅限日文的NextFire后端；不将其表述为优于默认后端。不要将该选项与`--mms-model-path`组合使用。

该后端只加载完整的本地快照`models/hf/nextfire-mms-ja-latn`。它没有运行时下载或回退，不使用通用Hugging Face缓存，也不执行远程代码。同一套原始音频/MSST人声双音轨审核以及`auto-fallback`或`strict`质量策略仍然适用。

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

Full-auto和分阶段MMS均接受`--output-mode subtitle-overlay`。该参数只改变渲染阶段，审计、MMS对齐、时间覆盖构建和companion SUG生成仍沿用原有契约。未提供背景视频时输出无音频、带透明通道的ProRes 4444 MOV；加入`--background-video <视频素材>`后由FFmpeg直接合成为AV1/AAC，较长素材会被裁剪，较短素材的剩余歌曲区间显示黑幕。渲染会依次探测`av1_nvenc`和`libaom-av1`，硬件编码不可用或执行失败时自动改用软件编码，并在报告中记录尝试历史。

## 已有SUG路径

使用`scripts/run_karaoke_japanese_workflow.py`重新渲染已经审核或手动调整的SUG。该路径生成当前布局和视频，但不准备MSST、不构建初始时间，也不调用MMS。

已有SUG路径同样接受`--output-mode subtitle-overlay`和可选的`--background-video`。

## 模型与缓存边界

- 将模型权重放在`models`下，绝不放在`.cache`中。
- 可选NextFire权重只能位于`models/hf/nextfire-mms-ja-latn`，不要提交到仓库。
- 将MSST解码输入、分离人声、运行时文件及识别/对齐缓存记录放在`.cache`下。
- 不要隐式下载缺失的模型。
- 在报告中记录模型和产物身份，不要使用媒体哈希作为质量或流程门禁。

## 日文注音与发布

在选定SUG中保留已审核注音。纯片假名不接收单独注音，被忽略的过时纯片假名注音不得改变输入。MMS时间不得改写注音或冻结显示文本。

注音人工审核默认是可选项。缺失、过期、仅机器生成、未批准或无法读取的注音审核sidecar会记录为未执行，不会阻止渲染。只有显式需要批准门禁时才选择`--pronunciation-validation required`。注音或SUG结构错误在所有模式下仍是硬失败。

默认交付是带硬字幕和AAC-LC的AV1`yuv420p`MP4。MKV/FLAC和完整空解码仍是显式选项。在每次新运行中生成当前布局，并在提升前执行常规字幕、颜色、流、时长和代表帧门禁。
