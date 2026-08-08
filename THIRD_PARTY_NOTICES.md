# Third-Party Notices

[简体中文](THIRD_PARTY_NOTICES.zh-CN.md) | English

## MMS_FA model

The optional forced-alignment workflow can load the `MMS_FA` model through
TorchAudio. The model weights are not distributed by this repository.

TorchAudio identifies the model as work published by the authors of *Scaling
Speech Technology to 1,000+ Languages* under the
[Creative Commons Attribution-NonCommercial 4.0 license (CC-BY-NC-4.0)](https://creativecommons.org/licenses/by-nc/4.0/).
Use of the model weights, including attribution and non-commercial limits, is
governed by that license. See the
[TorchAudio MMS_FA documentation](https://docs.pytorch.org/audio/2.7.0/generated/torchaudio.pipelines.MMS_FA.html).

The bootstrap does not download these weights unless the user explicitly
passes `--accept-mms-cc-by-nc-4-0`, acknowledging the attribution requirement
and non-commercial restriction. A source-and-license sidecar is written beside
weights downloaded by the bootstrap.

## Optional NextFire MMS Japanese-Latn model

`NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn` is an experimental,
Japanese-only optional alignment model. Its model card is AGPL-3.0-only; its
base model, `facebook/mms-300m`, is CC BY-NC 4.0. The optional installer
requires both `--accept-nextfire-agpl-3-0` and
`--accept-mms-cc-by-nc-4-0` before downloading missing files. The weights are
not distributed or committed by this repository.

## OpenAI Whisper model weights

The optional recognition workflow can use OpenAI Whisper `base.pt`. The model
weights are not distributed by this repository. OpenAI publishes Whisper code
and model weights under the [MIT License](https://github.com/openai/whisper/blob/main/LICENSE).
The bootstrap records the source URL, checksum, and license metadata beside a
downloaded Whisper checkpoint.

## FFmpeg binaries

`imageio-ffmpeg` may provide a platform FFmpeg executable used by the rendering
scripts. The license configuration of an FFmpeg build depends on how that
binary was compiled. See the
[imageio-ffmpeg project](https://github.com/imageio/imageio-ffmpeg) and the
[FFmpeg legal information](https://ffmpeg.org/legal.html) for the applicable
binary notices and source terms.
