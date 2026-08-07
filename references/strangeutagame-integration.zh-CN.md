# StrangeUtaGame集成

[English](strangeutagame-integration.md) | 简体中文

本集成包用于向兼容的StrangeUtaGame工作树添加卡拉OK制作流程。依赖清单定义安装的脚本、共享模块、包文件和支持工具。

## 安装

先预览复制计划，再执行安装：

```powershell
$skillRoot = (Resolve-Path .).Path
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target D:\path\to\StrangeUtaGame --dry-run
python "$skillRoot/scripts/install_strangeutagame_integration.py" --target D:\path\to\StrangeUtaGame
```

安装器检查`pyproject.toml`、`src/strange_uta_game`和`scripts`。它只复制`integration/strangeutagame/dependency-manifest.json`中列在`scripts`、`shared_modules`和`package_files`下的路径。目标文件不同时需要显式决定是否覆盖，原文件会备份到`.karaoke-skill-backup/<UTC stamp>/`。

## 环境

Python 3.12是测试基线；公开脚本要求Python 3.10或更新版本。复用工作树已有的`.venv`，普通命令使用`uv run --no-sync`：

```powershell
Set-Location D:\path\to\StrangeUtaGame
if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
  uv sync
}
uv run --no-sync python --version
```

另行安装`ffmpeg`和`ffprobe`，并确认libass与可用的AV1编码器。Rubber Band只在变调时需要；Whisper、MMS、MSST和CJK字体按制作配置选择。

当前兼容性测试基线为StrangeUtaGame 1.4.5和SUG存储格式0.3.0。应用版本从`src/strange_uta_game/__version__.py`读取，存储格式从`SugMigrator.CURRENT_VERSION`读取。

## 项目配置

复制`examples/album.example.json`，替换其中的占位内容，然后通过`--manifest`或`KARAOKE_ALBUM_MANIFEST`传入生成的清单。

歌曲专用的显示、注音分组和时间读音决定可分别通过`KARAOKE_DISPLAY_OVERRIDES`、`KARAOKE_RUBY_GROUP_OVERRIDES`和`KARAOKE_TIMING_READING_OVERRIDES`提供。日语工作流提供`optional`、`required`和`off`三种注音模式，默认为不阻塞的`optional`。多演唱者身份和顶部叠加规则见[singer-overlays.zh-CN.md](singer-overlays.zh-CN.md)。

## 制作顺序

```text
清单 -> 日语工作流 -> 可选MSST证据 -> ASR/MMS复核
-> 源歌词 -> 候选注音写入规范SUG -> 上下文注音审核
-> 时间与分句决定 -> 只读渲染器 -> ASS/报告/画面
-> 构图 -> AV1渲染 -> 媒体检查 -> 最终化 -> 归档
```

规范的可编辑时间轴来源是`.sug`项目。候选生成只填补缺失注音，审核把接受的修正写回规范SUG，渲染器读取审核后的项目且不再推断新注音。

请求变调时，在时间轴和渲染前对完整混音运行`scripts/pitch_shift_audio.py`。验证后的变调音频作为证据、预览和封装所选用的音频源。

## 视觉约定

黑胶保持旋转，正式与测试流程使用`direction-neutral-concentric-grooves/v3/backplate-absent`重新生成黑胶。

当前宽屏构图为`wide-layout-v6/top-secondary-clearance`：黑胶卡`(40,30,340,402)`，footer底部留白`12`，底部字幕面板从`y=576`开始。右侧大框和黑胶小背板均不存在。频谱版本使用安全区域`(736,226,1168,348)`，水平辉光余量64 px，上下辉光余量56 px，柱体上下安全余量8 px。secondary叠加层使用锚点`y=12`、默认字号`60 px`、最低`36 px`、内容安全带`y=0..96`，实际outline/glow保留区延伸到`y=107`；标题label/title/artist位置为`y=120/155/220`，标题区使用实际ink bounds并与保留区至少保持`16 px`间距。

专辑直出入口按编码器区分：`render_karaoke_direct_av1_420_album.py`是AV1 4:2:0命令，`render_karaoke_direct_hevc444_album.py`是HEVC 4:4:4命令。旧名称`render_karaoke_direct_av1_album.py`仅作为HEVC的弃用兼容入口保留。两个编码通道都通过中性的`karaoke_direct_album_planning.py`处理清单选择和任务规划。

## 共享单曲workflow

共享一键入口为`scripts/run_karaoke_japanese_workflow.py`。
`--visual-style vinyl|spectrum`默认使用`vinyl`，每次运行都必须提供不存在的全新`--output-dir`：

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --composition <composition-png> --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style vinyl --vinyl <canonical-vinyl-png>
```

频谱使用`--visual-style spectrum`并省略`--vinyl`；可选的
`--spectrum-color RRGGBB`和`--progress-color RRGGBB`只在频谱模式有效。黑胶的`--vinyl`是身份输入；workflow会在新输出目录中重新生成并校验当前旋转资源，再把生成资源传给渲染。频谱不要求、不探测、不生成、不传递也不报告vinyl。

workflow先独立写入`karaoke-preflight.ass`，再在MP4渲染阶段写入最终
`karaoke.ass`，并要求两者SHA-256身份一致。默认使用完整时长且只生成MP4；`--lossless-companion`和`--full-decode`是MKV与完整解码诊断的显式opt-in。日文注音验证默认为不阻塞的`optional`。一键workflow与底层renderer使用相同的歌手、叠加层、注音、容器和诊断门禁。专辑/批量direct renderer仍仅支持vinyl，不能把它写成支持频谱的路径。

## 安装文件

公开工作流入口为`scripts/run_karaoke_japanese_workflow.py`，由`scripts/karaoke_workflow.py`协调。共享代码位于`karaoke_common/`，日语布局代码位于`karaoke_japanese/`。

兼容性检查器保留在Skill仓库中。使用目标工作树的项目本地Python运行兼容性检查和环境检查：

```powershell
Set-Location D:\path\to\StrangeUtaGame
uv run --no-sync python D:\path\to\skill\scripts\check_karaoke_environment.py --target .
uv run --no-sync python D:\path\to\skill\scripts\check_sug_compatibility.py --repo . --project D:\path\to\project.sug
```
