# NPK-X

蛋仔派对 NPK 便携工具包

> EggyFly Wiki社群 QQ群: `768415827`

keywords: `unnpk` `unpacknpk` `nxpk` `ppk` `netease` `eggyparty` `蛋仔派对` `npk解包` `蛋仔派对解包`

Windows x64 便携版：**无需安装 Python、无需 pip、无需 ffmpeg**。解压后即可读取本地客户端 NPK，按真实索引路径解包

> 仅用于本机资源的离线研究与备份。工具只读游戏目录，不启动、不注入、不修改客户端。本项目全程使用AI进行辅助开发。

## 0. 在线浏览（推荐）

```bat
run_browser.bat "<party_pc path>"
```

> 上述 <party_pc path> 指的是蛋仔派对PC客户端的安装目录，在所有doc中亦如此

启动后会自动打开本地页面（默认 `http://127.0.0.1:8765/`），可以直接：

* 按真实逻辑路径浏览目录、搜索路径，并按名称/大小/类型/来源包排序；搜索、路径、排序和视图控制在滚动时固定在内容区顶部；
* KTA/DDS/KTX 等纹理显示缩略图，图片按需转 PNG，WEM/BNK 即时转 FLAC 播放（不保存全量 FLAC）；
* BNK 可查看内部数据块、HIRC 对象统计和全部内嵌 WEM，并逐条试听；
* `.animgraph/.animation/.gim/.mtg/.sfx/.efx/.mesh/.csb` 等资源会显示类型说明、可读字段、引用路径和 Hex，不再只有十六进制；
* 右侧固定索引栏可查看当前路径、文件类型统计、子目录并快速跳转；
* 视频 Range 播放、文本/JSON/XML 预览、二进制十六进制预览与下载；
* 在预览窗口中通过资源管理器/访达、VS Code 或 IntelliJ IDEA 打开临时副本。

首次启动会建立约 200 MB 的索引数据库，图片/缩略图缓存默认上限 2 GB。详细说明见 `docs/NPK_BROWSER.md`。

如果确实需要把全部资源落到磁盘，再使用下面的 `run_extract.bat`。

## 1. 快速开始（全量导出）

1. 将整个文件夹解压到任意位置（路径可以包含中文）。
2. 先运行自检：

   ```bat
   run_selfcheck.bat
   ```

   应看到 `SELF-CHECK OK`。若只有 `soundfile missing` 提示，WEM 转 FLAC 会退回 ffmpeg；本包已内置 soundfile，不应出现。

3. 探测游戏包与版本（强烈建议，尤其游戏更新后）：

   ```bat
   run_probe.bat "<party_pc path>"
   ```

   参数是包含 `char.npk`、`gui2.npk` 等文件的目录。探测只读，不写任何文件。

4. 全量解包与转换：

   ```bat
   run_extract.bat "<party_pc path>" "<export path>"
   ```

   > 此处 `<export path>` 应为你实际导出的路径, 下述同

   第二个参数是输出目录。省略时会使用当前目录下的 `export_mode3`。首次全量输出约 **48 GB**，建议至少留出 **60 GB** 磁盘空间。

5. 校验产物：

   ```bat
   run_verify.bat "<export path>"
   ```

6. 生成路径/偏移清单（可选）：

   ```bat
   run_manifest.bat "<party_pc path>" "<export path>\manifest.csv.gz"
   ```

## 2. 输出规则

* 有真实路径的资产：按索引路径输出，目录层级一致。
  * `.kta` / `.dds` / `.ktx` / `.tga` / `.bmp` / `.webp` / `.gif` / `.jpg` → 同名 `.png`
  * `.wem` → 同名 `.flac`（vgmstream 解码 + soundfile 无损 FLAC）
  * `.mesh`、`.gim`、`.mtg`、`.csb`、`.bnk`、`.json` 等 → 原名原样
* 纹理只导出基础 mip；PNG 是 DXT 解码后的无损像素，不是原始 DDS 数据。
* 二维 ASTC KTX1 贴图可转换为 PNG；无法转换的 cubemap DDS、数组/特殊 KTX、DXT3 等保留原始字节，状态为 `fallback_original`。
* 路径词典未收录的资产 → `_unresolved/<package>/<signature>.*`
* `script.npk` / `script_patch.npk` 是独立脚本命名空间 → `_script/<package>/<signature>.xn`
* 所有输出都在 `_manifest.csv.gz` 中有逐条记录（状态、源路径、输出路径、偏移、长度）。

## 3. 目录结构

```text
README.md                     本文件
run_browser.bat               本地 NPK 网站（按需预览，推荐）
run_rebuild_data.bat          从客户端 PPK 重建路径词典
run_wwise_map.bat             生成 WEM 数字 ID 到所属 BGM Bank 的映射
run_extract.bat               全量解包/转换
run_probe.bat                 版本/密钥/索引探测（只读）
run_verify.bat                产物校验（PNG/FLAC/路径缺失/重复）
run_manifest.bat              生成 path -> entry 清单
run_validate.bat              全包索引/载荷校验
run_selfcheck.bat             工具自身自检
bin/python/                   内置 Python 3.12 x64 与依赖（无需安装）
bin/vgmstream/                Wwise WEM 解码器
tools/                        解码/提取/校验脚本（含 npk_browser.py、bnk_parse.py、rebuild_data.py、map_wwise_media.py）
web/                          浏览器页面（HTML/CSS/JS）
data/mode3_profile.json       当前版本密钥/路径 seed/版本指纹（可选，代码有内置默认值）
data/pathdict.txt.gz          542,346 条逻辑路径词典（gzip，可重建）
data/toolkit.json             工具包构建信息（可选）
fixtures/                     密码与路径固定测试向量
docs/                         格式、提取说明、版本兼容指南
licenses/                     第三方许可证与版权信息
```

## 4. 版本兼容性（重要）

NPK 的 mode 3 索引密钥和自定义密码表**与客户端构建绑定**，游戏大版本更新后可能变化。工具在解包前会验证索引（排序、偏移、压缩类型、Zstd 帧头）；不匹配会直接报错并拒绝写输出，避免生成垃圾文件。

* 同版本/同渠道：直接使用。
* 只有密钥变化：用 `--key-hex` 或新 profile（见 `docs/VERSION_COMPATIBILITY.md`）。
* 密码表/入口格式变化：需要更新 `mode3_tables.py` 或代码；`run_probe.bat` 会先报错。
* 只新增资源：索引仍可用；未收录路径进入 `_unresolved`，更新 `data/pathdict.txt.gz` 后可恢复真实名称。

完整说明见 `docs/VERSION_COMPATIBILITY.md`。

## 5. 浏览与导出的取舍

* **在线浏览**：无需解包，先建约 200 MB 目录；图片/缩略图按需缓存（默认 2 GB 上限），音频即时流式播放，不保存 FLAC。
* **全量导出**：会生成约 48 GB 文件，适合需要离线批量处理的场景。

`data/` 说明：`pathdict.txt.gz` 决定真实文件名，缺失时浏览器会自动从客户端 `ppk/` 重建；`mode3_profile.json` 是当前构建的密钥/版本快照，缺失时使用代码内置默认值；`toolkit.json` 只是构建信息，可以删除。密钥与密码表仍与客户端构建绑定，游戏大版本更新后若校验失败，需要更新 profile 或密钥，不能只靠重建路径词典解决。

## 6. 已知边界

* `script.npk` / `script_patch.npk` 的脚本路径名无法在本地恢复；条目/载荷仍会解包为 `_script/.../<signature>.xn`。
* 当前版本仍有 774 条资产路径不在 542,346 条词典快照中，使用哈希名；不影响资源内容。
* cubemap/数组纹理保留原始文件；单个 cubemap 无法用一张 PNG 表示。
* `.bnk` 全量导出时仍原样保留；在线浏览器可以解析其内部 DIDX/DATA/HIRC 结构并逐条预览内嵌 WEM。
* `wwise/*.wem` 的数字文件名是 Wwise 媒体 Short ID，不是工具改名；客户端没有附带 `SoundbanksInfo.xml`，原始事件名无法从资源本身恢复。可用 `run_wwise_map.bat` 生成“WEM → 所属 BGM Bank”的对照表，或按 Bank 名做别名复制，详见 `docs/NPK_BROWSER.md`。

## 7. 命令行（可选）

如果不想用 `.bat`，可直接调用内置 Python：

```bat
bin\python\python.exe tools\npk_browser.py --game-root "<party_pc path>"
bin\python\python.exe tools\extract_mode3.py --game-root "<party_pc path>" --root "<export path>" --jobs 6
bin\python\python.exe tools\probe_mode3.py --game-root "<party_pc path>"
bin\python\python.exe tools\verify_export.py "<export path>"
bin\python\python.exe tools\validate_npk_mode3.py --game-root "<party_pc path>"
```

更多参数：`--path-dict`、`--profile`、`--key-hex`、`--tables`、`--packages`、`--limit`、`--no-script`、`--force-empty`。

路径词典重建：

```bat
bin\python\python.exe tools\rebuild_data.py --game-root "<party_pc path>" --output data\pathdict.txt.gz --force
```

Wwise 数字 WEM 别名映射（原文件不动）：

```bat
run_wwise_map.bat "<party_pc path>" "D:\wwise_media_map.csv"
run_wwise_map.bat "<party_pc path>" "D:\wwise_media_map.csv" --copy-to "D:\EggyWwise"
```

> 别忘了改路径!!! 这里就直接示例了

批处理也可透传额外参数，例如：

```bat
run_extract.bat "<party_pc path>" "<export path>" --key-hex <32位hex>
run_probe.bat "<party_pc path>" --key-hex <32位hex>
```

## 8. 第三方组件

* Python 3.12（PSF License）
* Pillow（HPND）
* zstandard（BSD）
* mmh3（MIT）
* numpy（BSD）
* soundfile / libsndfile（BSD / LGPL）
* texture2ddecoder（MIT）
* vgmstream（见 `bin/vgmstream/COPYING`）

完整许可见 `licenses/` 与 `THIRD_PARTY_NOTICES.md`。本工具不包含游戏客户端、NPK/PPK 包或任何游戏资源。
