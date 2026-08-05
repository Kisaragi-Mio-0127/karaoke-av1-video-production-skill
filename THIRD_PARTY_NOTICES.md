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
| MSST runner and models | optional separated-vocal evidence | User-supplied; no repository or model is bundled or selected by this project |
| CJK fonts | subtitle rendering | User-supplied; redistribution is not implied by installation or use |

The dependency versions are recorded in
`integration/strangeutagame/requirements/requirements-karaoke.lock.txt`. A lock
version is not a grant of rights; preserve any required notices when packaging
or distributing an application environment.
