# Copyright and modification notice

Unless a file says otherwise, this repository's code and documentation are
distributed under **GPL-3.0-only**. Copyright remains with the respective
authors and contributors.

The included `LICENSE` is GPL-3.0-only; this repository does not make an AGPL
license claim. Do not infer an AGPL grant for separately supplied components.

The files under `integration/strangeutagame/scripts/` are later-developed
integration scripts, not files from the Git history of
[karaoke-studio/StrangeUtaGame](https://github.com/karaoke-studio/StrangeUtaGame).
They are validated against StrangeUtaGame 1.4.5 and SUG storage format 0.3.0.
The application version must match in `__version__.py` and `pyproject.toml`.
These files were untracked additions in the production working tree before
packaging. Some scripts directly import tracked
StrangeUtaGame application modules, some depend on those scripts transitively,
and others are release helpers with no upstream-code import. The machine-readable
classification is `integration/strangeutagame/dependency-manifest.json`.

Changes made for this repository through 2026-08-06 include:

- removal of real album names, lyrics, media hashes, cover URLs, and local paths;
- explicit manifest/environment configuration instead of import-time private data;
- external JSON for song-specific display and ruby decisions;
- opt-in, constrained network access;
- a guarded StrangeUtaGame installer and release-safety tests.

This repository does not redistribute the StrangeUtaGame application package.
The integration file list and dependency boundary are maintained in the README
and `integration/strangeutagame/dependency-manifest.json`. See
`THIRD_PARTY_NOTICES.md` for runtime components that are referenced but not
redistributed.

This repository does not redistribute recordings, lyrics, fonts, cover art,
FFmpeg/Rubber Band binaries, drivers, model files, or rendered media. Users are
responsible for their licenses, service terms, and redistribution rights.

## 中文说明

除非具体文件另有说明，本仓库代码和文档使用**GPL-3.0-only**，著作权归各自作者与贡献者所有。本仓库不声明AGPL授权，也不能据此推断外部组件获得了AGPL授权。

`integration/strangeutagame/scripts/`中的文件是后来开发的集成脚本，不是从[karaoke-studio/StrangeUtaGame](https://github.com/karaoke-studio/StrangeUtaGame)上游Git历史复制的文件。当前要求并验证的版本为StrangeUtaGame 1.4.5和SUG 0.3.0；`__version__.py`与`pyproject.toml`中的应用版本必须一致。各脚本的直接依赖、传递依赖和无上游代码导入分类以`integration/strangeutagame/dependency-manifest.json`为准。

公开快照已删除真实专辑名、歌词、媒体哈希、封面URL和本机路径；歌曲专用分句和ruby规则改由外部JSON提供；网络访问需显式启用。仓库不重新分发StrangeUtaGame应用、录音、歌词、字体、封面、FFmpeg或Rubber Band二进制文件、驱动、模型和渲染媒体。使用者须自行确认许可证、服务条款和再分发权。
