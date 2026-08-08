# 第三方组件说明

[English](THIRD_PARTY_NOTICES.md) | 简体中文

## MMS_FA模型

可选的强制对齐流程可以通过TorchAudio加载`MMS_FA`模型。本仓库不分发该模型权重。

TorchAudio说明该模型来自论文*Scaling Speech Technology to 1,000+ Languages*的作者，并采用[知识共享署名—非商业性使用4.0许可协议（CC-BY-NC-4.0）](https://creativecommons.org/licenses/by-nc/4.0/)。模型权重的使用受该许可协议约束，包括署名要求和非商业用途限制。详情见[TorchAudio MMS_FA文档](https://docs.pytorch.org/audio/2.7.0/generated/torchaudio.pipelines.MMS_FA.html)。

只有用户显式传入`--accept-mms-cc-by-nc-4-0`，确认接受署名要求和非商业用途限制后，Bootstrap才会下载这些权重。Bootstrap会在下载的权重旁写入来源和许可证sidecar文件。

## 可选NextFire MMS日文-Latn模型

`NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn`是实验性、仅限日文的可选对齐模型。其模型卡采用AGPL-3.0-only；基础模型`facebook/mms-300m`采用CC BY-NC 4.0。可选安装器下载缺失文件前必须同时传入`--accept-nextfire-agpl-3-0`和`--accept-mms-cc-by-nc-4-0`。本仓库不分发也不提交这些权重。

## OpenAI Whisper模型权重

可选的识别流程可以使用OpenAI Whisper的`base.pt`。本仓库不分发该模型权重。OpenAI以[MIT许可证](https://github.com/openai/whisper/blob/main/LICENSE)发布Whisper代码和模型权重。Bootstrap会在下载的Whisper检查点旁记录来源URL、校验值和许可证元数据。

## FFmpeg二进制文件

`imageio-ffmpeg`可能提供渲染脚本使用的平台FFmpeg可执行文件。FFmpeg构建的许可证配置取决于该二进制文件的编译方式。适用的二进制文件说明和源码条款见[imageio-ffmpeg项目](https://github.com/imageio/imageio-ffmpeg)与[FFmpeg法律信息](https://ffmpeg.org/legal.html)。
