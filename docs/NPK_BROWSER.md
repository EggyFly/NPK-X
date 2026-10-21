# 本地 NPK 浏览器（NPK Browser）

`npk_browser.py` 提供一个只读的本地网站，直接读取游戏目录中的 NPK，按真实索引路径建立虚拟文件系统。它不需要先做 48 GB 甚至更大占用的全量解包，图片和音频都按需解码。

## 启动

在工具包根目录运行：

```bat
run_browser.bat "<party_pc path>"
```

或直接调用内置 Python：

```bat
bin\python\python.exe tools\npk_browser.py --game-root "<party_pc path>"
```

> 上述 <party_pc path> 指的是蛋仔派对PC客户端的安装目录，在所有doc中亦如此

首次启动会扫描/解密所有 NPK 索引，建立一份 SQLite 目录（约 200 MB，放在 `%LOCALAPPDATA%\EggyNpkBrowser`），随后启动浏览器页面。之后启动会复用目录，只需数秒。若 `data/pathdict.txt.gz` 不存在，浏览器会尝试从游戏 `ppk/` 分片重建路径词典并保存到缓存目录；不会修改游戏目录。

## 功能

* **目录浏览**：按真实逻辑路径列出所有资源；网格和列表视图都会显示缩略图：KTA/DDS/KTX 等图片、WEM/BNK 音频波形、视频首帧，以及 JSON/XML/LOD 等文本的前几行内容；无法可视化的类型显示类型标签，无需点开详情面板。打开详情时，列表/网格中对应条目会高亮（列表为左侧强调条，网格为描边），关闭详情后自动取消。`_unresolved/` 是路径词典未收录的条目，`_script/` 是脚本命名空间。
* **排序**：名称 A–Z / Z–A、大小升/降序、按类型、按来源包；目录和搜索结果共用当前排序。
* **固定顶栏**：搜索框、当前路径、排序方式和网格/列表切换固定在内容区顶部，滚动时始终可见；右侧索引栏会自动避开顶栏，不会重叠。
* **当前目录索引**：右侧固定栏显示当前路径、含子目录的文件总量与大小、文件类型筛选、子目录快速跳转、上一级和复制路径。页面滚动时保持可见。
* **搜索**：按真实路径模糊搜索（最多 300 条结果），支持当前排序。
* **图片**：`.kta`/`.dds`/`.ktx`/`.tga`/`.jpg` 等首次查看时按需解码为 PNG；网格/列表缩略图按尺寸缓存，原图可新窗口查看或下载。
* **音频**：`.wem`/`.bnk` 由 vgmstream 即时解码，soundfile 即时编码 FLAC 并流式返回；**不保存全量 FLAC**。列表缩略图会解码前 60 秒生成波形 PNG 并缓存，包含内嵌 WEM 的 Bank 显示首条音频的波形。
* **BNK 结构预览**：`.bnk` 预览会显示 Wwise 版本、Bank ID、数据块（BKHD/DIDX/DATA/HIRC/STID 等）、HIRC 对象类型统计，以及全部内嵌 WEM 的 ID、编码、声道、采样率和大小；点击任意一条即可试听该条内嵌音频。没有内嵌音频的事件 Bank 会显示 HIRC 结构。
* **视频**：`.mp4`/`.webm` 等原始解压数据，支持 Range 拖动。
* **文本**：JSON/XML/`.gim`/`.mtg`/`.lod` 等直接以文本显示；二进制显示 4 KB 十六进制；均可下载。
* **通过…打开**：预览窗口可将当前资源交给资源管理器（Windows）/访达（macOS）、VS Code、IntelliJ IDEA。打开的是缓存目录中的临时副本；图片会优先使用转换后的 PNG。
* **元数据**：路径、包名、signature、offset、压缩/解压大小、压缩方式、flags、两个 MurmurHash3。
* **大目录保护**：单目录最多返回 3,000 条文件，顶部提示用搜索定位其余项目。
* **缓存**：仅缓存 PNG/缩略图，默认上限 2 GB（`--cache-limit-mb`）；音频不落盘。目录 SQLite 可删除，下次启动自动重建。

## 常用参数

```text
--game-root <目录>      包含 *.npk 的目录（必填或自动探测）
--port 8765             端口；被占用时自动尝试后续端口
--host 127.0.0.1        默认只监听本机
--cache-dir <目录>      目录数据库和图片缓存位置
--cache-limit-mb 2048   PNG/缩略图缓存上限
--rebuild-catalog       强制重扫 NPK
--refresh-pathdict      从游戏 PPK 分片重新提取路径词典
--no-open               不自动打开浏览器
--profile <json>        版本 profile
--key-hex <32位hex>     临时覆盖 mode 3 密钥
--tables <py>           替换密码表模块
```

## Wwise 数字文件名

`wwise/` 下的 `.wem` 文件名（例如 `1000688149.wem`）是 Wwise 的媒体 Short ID，不是本工具生成的哈希，也不是索引损坏。当前客户端的路径词典里 1,009 个 `.wem` 全部是这种数字形式；这正是资源发布时的真实名称。

Wwise Bank 的 HIRC 只保存数字对象 ID。实测 4,784 个 Bank 中只有 1 个带 STID 字符串块，且客户端没有附带 `SoundbanksInfo.xml`/Wwise 工程文件，因此**无法从游戏资源本身还原原始事件名或中文语音名**。可以做的是生成可读别名：

* 954/1,009 个独立 WEM 被命名清晰的 BGM Bank（HIRC Music Track）引用，例如 `wwise/bgm_s20_speed.bnk` 引用 `466729056.wem`；可用所属 Bank 名作为别名目录，但不要覆盖原 ID。
* 如果你有原始 Wwise 工程或 `SoundbanksInfo.xml`，可以用其中的 Short ID → 名称映射做真正的重命名。
* `run_wwise_map.bat "<party_pc path>"` 会生成 `wwise_media_map.csv`，列出每个 WEM 的所属 Bank 和 Music Track 对象 ID；加 `--copy-to D:\EggyWwise` 可把 WEM 复制到 `bank 名/ID.wem` 的别名目录（原文件不动）。
* 浏览器不会自动改名，以免破坏依赖；BNK 预览会直接显示每条内嵌 WEM 的 ID 和 Bank 上下文。

## 其他资源格式与在线预览

浏览器现在会把非媒体资源分为“文本/XML”和“二进制结构”两类，并显示可读字段、引用路径和 Hex：

* `.animgraph` / `.animation` / `.animconfig` / `.trackgroup` / `.lod` / `.spr`：NeoX 的 XML/文本资源，会直接显示内容，并列出引用到的动画、骨骼、特效或贴图。
* `.gim`：NeoX 对象/预制体配置，引用骨骼、子网格、材质和挂点，不是可单独显示的模型。
* `.mtg`：NeoX 材质配置，包含材质槽、着色器技术和贴图引用。
* `.sfx` / `.efx`：NeoX 特效/粒子参数，引用粒子材质和贴图；需要特效播放器才能显示。
* `.gmp`：地图/场景数据，需要地图地形、对象和材质格式才能可视化。
* `.xn`：受保护的 NeoX 脚本容器，不是可视化资源。
* `.qm`：Qt Linguist 翻译表，包含界面文案。
* `.csb`：Cocos Studio UI 布局，包含节点树和图片/动画引用。
* `.mesh` / `.skeleton` / `.rawanimation`：模型几何、骨骼和原始动画数据；当前客户端的 `.mesh` 顶点数据有打包/量化变体，尚未接入通用 3D 预览。

可以在线预览的是：图片纹理、音频/BNK、视频、文本/XML、以及上述二进制格式的结构和引用信息。完整 3D 模型、骨骼动画、粒子特效、地图和 Cocos UI 渲染需要对应的引擎模块，不是仅靠单个资源文件就能还原。

## data/ 目录与重建

| 文件 | 作用 | 删除后的影响 |
| --- | --- | --- |
| `data/pathdict.txt.gz` | 542,346 条逻辑路径；用于把索引 signature 映射成真实名称 | 仍能浏览/解码，但所有条目会落到 `_unresolved/<package>/<signature>.*`；浏览器会自动尝试从游戏 `ppk/` 重建，或用 `run_rebuild_data.bat` |
| `data/mode3_profile.json` | 当前客户端构建的密钥、路径 seed、已知包指纹 | 当前版本仍可使用代码内置的默认密钥/seed；若游戏更新后密钥或表变化，需要新的 profile |
| `data/toolkit.json` | 工具包版本和构建信息，仅作文档元数据 | 无影响，运行时不读取 |

`run_rebuild_data.bat "<party_pc path>"` 会扫描游戏 `ppk/` 分片，找到其中的路径清单 Zstd 帧，重新生成 `data/pathdict.txt.gz`。浏览器在路径词典缺失时也会自动做这件事，并把生成结果缓存到 `%LOCALAPPDATA%\EggyNpkBrowser\pathdict.generated.txt.gz`；加上 `--refresh-pathdict` 可强制重新提取。`rebuild_data.py --write-profile --profile-output <json>` 可以生成一份使用内置 key/seed 的最小 profile，但路径词典可以重建，mode 3 密钥仍与客户端构建绑定，不能用 NPK 本身反推；密钥变化时需要更新 profile 或 `--key-hex`。

## 安全与边界

* 只读：不会修改 NPK 或游戏目录。
* 默认只绑定 `127.0.0.1`；不要在不可信网络上使用 `--host 0.0.0.0`。
* 所有路径来自本地目录数据库，不存在文件系统路径穿越；不提供上传。
* 图片解码/音频解码在本地进行，可能占用 CPU；大图/长音频首次打开需要等待。
* “通过…打开”只接受本地目录数据库中的条目；会在缓存目录生成临时副本，不会修改 NPK。VS Code / IDEA 未安装时会给出提示。
* 路径词典未收录的 774 条资产、48,871 条脚本仍以 `_unresolved` / `_script` 哈希名显示；内容可预览/下载。
* 脚本 `.xn` 为原始容器，网页只做十六进制/下载，不执行脚本。

## 版本更新

浏览器与解包器共用 `data/mode3_profile.json` 和 `data/pathdict.txt.gz`。游戏更新后：

1. 用 `run_probe.bat` 检查；密钥/表变化时浏览器会在建目录阶段报错，不会生成错误数据。
2. 目录指纹包含 NPK 大小/mtime、密钥、密码表哈希、路径词典哈希；任一变化会自动重建目录。仅升级浏览器页面不会让目录失效。
3. 新资产会出现在 `_unresolved`；运行 `run_rebuild_data.bat` 更新路径词典后重建目录即可恢复真实名称。
4. 如果只是删除了 `data/`，浏览器会自动尝试重建路径词典；其他工具可用 `run_rebuild_data.bat` 补齐。
