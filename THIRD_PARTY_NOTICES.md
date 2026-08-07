# Third-party runtime inventory

This repository stores no third-party binary or model payloads. The following
components are installed or supplied separately:

| Component | Purpose | License/source review |
|---|---|---|
| StrangeUtaGame | GUI, editable SUG model, exporters | GPL-3.0 declaration in the [upstream repository](https://github.com/karaoke-studio/StrangeUtaGame) |
| FFmpeg / ffprobe / libass | probing, subtitle burn-in, encoding | Build-dependent LGPL/GPL terms; review [FFmpeg Legal](https://ffmpeg.org/legal.html) and the selected build configuration |
| Rubber Band | optional pitch shifting | Review the [official project](https://breakfastquay.com/rubberband/) and the license of the installed build |
| NVIDIA driver/NVENC | optional hardware AV1/HEVC encoding | Review NVIDIA's driver and Video Codec SDK terms |
| Whisper, stable-ts, PyTorch, torchaudio | optional ASR/MMS timing evidence | Installed from the locked Python environment; review each package and model license before use |
| Pillow, imageio-ffmpeg, Mutagen, SoundFile, NumPy, pykakasi | artwork/media/timing support | Installed from the locked Python environment; package metadata remains the authoritative license record |
| PyQt6 / Qt, WinRT integration | StrangeUtaGame GUI and optional Windows input integration | Supplied by the separately installed application environment; review package and Qt licensing before redistribution |
| requests / Python HTTP clients | Optional source retrieval | Installed from the application environment; review package licenses and every external service's terms |
| MSST runner and models | optional separated-vocal evidence | User-supplied; no repository or model is bundled or selected by this project |
| CJK fonts | subtitle rendering | User-supplied; redistribution is not implied by installation or use |
| NetEase lyric endpoint and any font-download host | Optional network data sources | Review the service terms and content rights for the selected source |

The dependency versions are recorded in
`integration/strangeutagame/requirements/requirements-karaoke.lock.txt`. A lock
version is not a grant of rights; preserve any required notices when packaging
or distributing an application environment.

## 中文说明

本仓库不保存第三方二进制文件或模型。以下组件需另行安装或提供：

| 组件 | 用途 | 许可证或来源检查 |
|---|---|---|
| StrangeUtaGame | GUI、可编辑SUG模型、导出器 | 上游仓库声明GPL-3.0；以[上游仓库](https://github.com/karaoke-studio/StrangeUtaGame)为准 |
| FFmpeg、ffprobe、libass | 探测、字幕烧录、编码 | 条款取决于构建配置；检查[FFmpeg法律说明](https://ffmpeg.org/legal.html)和实际构建参数 |
| Rubber Band | 可选升降调 | 检查[官方项目](https://breakfastquay.com/rubberband/)和已安装构建的许可证 |
| NVIDIA驱动、NVENC | 可选AV1/HEVC硬件编码 | 检查NVIDIA驱动和Video Codec SDK条款 |
| Whisper、stable-ts、PyTorch、torchaudio | 可选ASR/MMS时间证据 | 从锁定环境安装；分别检查软件包和模型许可证 |
| Pillow、imageio-ffmpeg、Mutagen、SoundFile、NumPy、pykakasi | 图像、媒体和时间轴支持 | 从锁定环境安装；软件包元数据是许可证真源 |
| PyQt6、Qt、WinRT集成 | StrangeUtaGame GUI和可选Windows输入集成 | 由另行安装的应用环境提供；再分发前检查软件包和Qt许可证 |
| requests、Python HTTP客户端 | 可选来源获取 | 从应用环境安装；检查软件包许可证和各外部服务条款 |
| MSST程序和模型 | 可选分离人声证据 | 由使用者提供；本仓库不捆绑也不指定具体仓库或模型 |
| CJK字体 | 字幕渲染 | 由使用者提供；安装或使用不代表获得再分发权 |
| 网易歌词接口及字体下载站点 | 可选网络数据来源 | 按所选来源检查服务条款和内容权利 |

依赖版本记录在`integration/strangeutagame/requirements/requirements-karaoke.lock.txt`。版本锁定不等于授权；打包或分发应用环境时必须保留各组件要求的声明。
