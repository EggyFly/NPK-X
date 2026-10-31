# 蛋仔派对 NPK 按真实路径解包与转换

本目录说明 `export_mode3` 的生成方式。索引解密、路径哈希和全包校验见
`NXPK_MODE3_FORMAT.md`。

## 命令

```powershell
# 全量解包 + KTX/DDS/TGA/PNG 转 PNG + WEM 转 FLAC
python .\extract_mode3.py --jobs 6

# 只处理部分包 / 做小规模测试
python .\extract_mode3.py --root .\_extract_test --packages props_ui_model.npk

# 校验输出（PNG 可全量 verify，FLAC 检查文件头）
python .\verify_export.py .\export_mode3

# 另一个终端查看进度
Get-Content .\_extract_run.log -Wait
```

输出根目录：`<running path>\export_mode3`

## 命名规则

* 有真实索引路径的条目：直接使用索引路径，例如
  `gui2/pic/场景/图片.kta` → `export_mode3/gui2/pic/场景/图片.png`。
* 纹理转换：`.kta`、`.dds`、`.ktx`、`.tga`、`.bmp`、`.webp`、`.gif`、`.jpg`
  在可解码时输出为同名 `.png`（保留原始主文件名）。
* 音频：`.wem` → 同名 `.flac`（vgmstream 解码，ffmpeg 编码）。
* 非图片/音频资源（.mesh、.gim、.mtg、.csb、.json、.bnk、.sfx 等）按原名原样写出。
* 纹理只导出基础 mip（不导出 mip 链）；DXT 本身有损，PNG 是无损保存解码后的像素。
* 无法转换的纹理/DDS cubemap/未知 KTX2 等保留原始字节，扩展名保持原样，并在
  `_manifest.csv.gz` 中标记 `fallback_original`；WEM 失败标记 `fallback_wem`。
* 索引哈希未命中本地路径词典的 774 条资产：写到
  `_unresolved/<package>/<signature>.png|.flac|.bin`，清单中 `real_path` 为空。
* `script.npk` / `script_patch.npk` 的 `XN` 容器没有本地脚本路径表：写到
  `_script/<package>/<signature>.xn`，清单中 `real_path` 为空。

## 本次结果（2026-10-20）

* 586,433 条索引记录：192,611 个转换 PNG、6 个原始 PNG、1,009 个 FLAC 音频、
  392,371 个原样复制资源、422 个保留原格式的回退文件、14 个空占位；
  共 586,433 个资源文件，约 48.22 GB。磁盘上 PNG 总数为 192,618
  （其中 1 个是回退的原生 PNG）。
* 全量校验：192,618 个 PNG 全部通过 Pillow `verify()`；1,009 个 FLAC 全部通过
  文件头检查，抽样 ffmpeg 解析成功；manifest 输出路径 0 重复、0 缺失；
  D 盘剩余约 21 GB。
* 536,788 条资产记录使用了真实逻辑路径；774 条本地路径词典未收录，
  使用 `_unresolved/<package>/<signature>`；48,871 条脚本使用
  `_script/<package>/<signature>.xn`。
* 回退原格式的 422 条：252 个 cubemap DDS、163 个不支持的 KTX1
  （cubemap/array/非二维块压缩）、4 个 glInternalFormat `0x93B0`、
  3 个 DXT3。它们保留原始字节，不是转换失败丢失。
  以上为当时的统计；现在已支持二维 ASTC（含 `0x93B0`），重新转换即可生成 PNG。
* 1,009 个 `.wem` 全部转为 `.flac`；4,784 个 `.bnk` 原样保留。

## 产物

* `export_mode3/<真实路径>`：转换后的资源树。
* `export_mode3/_manifest.csv.gz`：逐条记录 package、真实路径、输出路径、
  signature、偏移、长度、压缩标志、状态和输出大小。
* `export_mode3/_summary.json`：按包和状态的统计、磁盘余量。
* `export_mode3/_failures.jsonl`：转换失败或回退原始格式的记录（最多 5000 条）。
* `export_mode3/_verification.json`：`verify_export.py` 的全量校验结果
  （PNG/FLAC/缺失/重复路径）。
* 修复前的清单备份在 `_extract_work/pre_repair/`，不属于资源树。

## 外部工具

* `_tools/vgmstream/vgmstream-cli.exe`：解码 Wwise WEM（本机样本为 Custom Vorbis）。
* `<ffmpeg path>\bin\ffmpeg.exe`：把 vgmstream 输出的 WAV 编码为 FLAC。
* Python 依赖：`zstandard`、`Pillow`、`mmh3`、`texture2ddecoder`（DXT/BC 解码）。

如果没有 vgmstream/ffmpeg，WEM 会保留为 `.wem` 并记录 `fallback_wem`，不会丢失数据。
