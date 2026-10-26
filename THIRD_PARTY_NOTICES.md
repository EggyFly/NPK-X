# 第三方组件与许可声明

本工具包是离线资源研究/备份工具，不包含游戏客户端、NPK/PPK 包或任何游戏资源。使用本工具处理你合法拥有的本地文件；请遵守游戏服务条款和当地法律。

工具代码按“现状”提供，不附带任何明示或暗示担保。第三方组件归各自作者所有，按以下许可证分发：

| 组件 | 版本 | 许可证 | 许可证文件 |
| --- | --- | --- | --- |
| Python (embeddable) | 3.12.10 | PSF License | `licenses/PYTHON-3.12.10-LICENSE.txt` |
| Pillow | 13.0.0 | HPND | `licenses/pillow/LICENSE` |
| zstandard | 0.25.0 | BSD-3-Clause | `licenses/zstandard/LICENSE` |
| mmh3 | 5.3.1 | MIT | `licenses/mmh3/LICENSE` |
| numpy | 2.5.4 | BSD-3-Clause + bundled notices | `licenses/numpy/` |
| soundfile | 0.14.0 | BSD-3-Clause | `licenses/soundfile/soundfile-0.14.0.dist-info/LICENSE` |
| libsndfile (in soundfile wheel) | — | LGPL-2.1+ | `licenses/soundfile/libsndfile-COPYING` |
| texture2ddecoder | 1.0.6 | MIT | `licenses/texture2ddecoder/.../LICENSE` |
| cffi | 2.1.1 | MIT | `licenses/cffi/LICENSE` |
| pycparser | 3.11 | BSD-3-Clause | `licenses/pycparser/LICENSE` |
| typing_extensions | 4.16.0 | PSF-2.0 | `licenses/typing_extensions/LICENSE` |
| uncompyle6 | 3.9.3 | GPL-3.0（见其 COPYING） | `licenses/UNCOMPYLE6-COPYING.txt` |
| xdis | 6.1.7 | GPL-2.0（见其 COPYING） | `licenses/XDIS-COPYING.txt` |
| spark-parser | 1.9.0 | MIT | `licenses/SPARK-PARSER-LICENSE.txt` |
| click | 8.3.1 | BSD-3-Clause | `licenses/CLICK-LICENSE.txt` |
| six | 1.17.0 | MIT | `licenses/SIX-LICENSE.txt` |
| vgmstream-cli | r2117 (win64) | 见 COPYING | `licenses/VGMSTREAM-COPYING.txt` |
| Material Symbols Rounded (SVG paths) | — | Apache-2.0 | `licenses/MATERIAL-SYMBOLS-APACHE-2.0.txt` |

说明：

* 本包未内置 ffmpeg。WEM→FLAC 默认使用 vgmstream 解码 WAV，再用 soundfile/libsndfile 编码 FLAC；若用户另行提供 ffmpeg，程序会自动作为后备。
* uncompyle6 为 GPL-3.0、xdis 为 GPL-2.0，作为独立的外部进程调用（`python -m uncompyle6.bin.uncompile`），不与本工具代码链接成单一程序；二者随包分发，其许可证文本见 `licenses/`。
* vgmstream 二进制来自官方 `vgmstream-win64.zip` r2117，包含其运行 DLL；原 `COPYING`/`README`/`USAGE` 一并保留在 `bin/vgmstream/`。
* Python 依赖 wheel 的 `.dist-info` 目录保留在 `bin/python/Lib/site-packages/`，其中也包含元数据和许可证。
* 本项目自己的研究代码不复制任何第三方项目中的密钥材料或保护绕过逻辑；mode 3 结构、密钥与表来自对本机客户端构建的离线验证，详见 `docs/NXPK_MODE3_FORMAT.md`。
* `web/index.html` 内嵌的 Material Symbols Rounded 图标路径来自 [google/material-design-icons](https://github.com/google/material-design-icons)，按 Apache-2.0 使用（抓取 commit `10ecc14790612ca8fd164e3f4ffe9c16fc50d5b7`，2026-10-23）；项目内未引入图标字体文件。

如需再分发本工具包，请保留 `licenses/`、`THIRD_PARTY_NOTICES.md` 以及各第三方目录中的许可证文件。

- ACL (Animation Compression Library) 1.2.0, MIT License, https://github.com/nfrechette/acl — used by tools/acl_decode.exe for legacy NeoX ACL v4 animation clips.
- ACL (Animation Compression Library) 2.1.0 decoder binary `tools/acl_decode_v10.exe` from the NeoX Blender action add-on, MIT-licensed upstream ACL, used for legacy ACL v10 clips.
