# 构建与验证信息

- 工具包版本：1.5
- 构建日期：2026-10-21
- 内置 Python：3.12.10 embeddable win64
- 内置 WEM 解码器：vgmstream r2117 win64
- 版本 profile：`pc-1.0.32-engine-1791619829-a50-5119`

## 内置依赖

| 包 | 版本 |
| --- | --- |
| zstandard | 0.25.0 |
| Pillow | 13.0.0 |
| mmh3 | 5.3.1 |
| numpy | 2.5.4 |
| soundfile | 0.14.0 |
| texture2ddecoder | 1.0.6 |
| cffi | 2.1.1 |
| pycparser | 3.11 |
| typing_extensions | 4.16.0 |

## 构建时验证

- `selfcheck.py`：全部 PASS（密钥调度、分组向量、路径哈希、索引样本、依赖、vgmstream、路径词典）。
- `probe_mode3.py`：11 个本地 NPK 全部 `known=OK`，采样 Zstd 32/32（脚本包无压缩，0/0）。
- 便携 Python 解包 `props_ui_model.npk`：337 条记录，75 个 PNG，0 失败，PNG 全部通过 Pillow verify。
- 便携 Python + vgmstream + soundfile：单个 WEM 成功转 FLAC，文件头为 `fLaC`。
- 便携浏览器：目录 586,433 条、31,999 个目录；图片/缩略图/文本/音频 API 通过。
- 前端 1.2：1440×1000 下固定索引栏 `position: sticky`、排序/类型筛选/搜索结果排序、KTA 缩略图全部通过；冷缓存并发缩略图 48/48 成功。
- “通过…打开”：Windows 资源管理器、VS Code 实际启动成功；IDEA 未安装时返回明确提示；Origin/JSON 校验拦截跨站请求。
- BNK 解析：4,784 个 Bank 全部解析成功，58,900 条内嵌 WEM；10/10 抽样经 vgmstream 解码成功；HIRC 事件 Bank 结构显示正常。
- 路径词典重建：从 `ppk/00014d45` 首个 Zstd 帧重新生成 542,346 行，与现有字典逐字节一致（29,767,881 字节）。
- 删除 `data/pathdict.txt.gz` 后，浏览器自动重建逻辑已接入并用独立无 `data/` 副本实测：自动生成 542,346 行路径词典、重建 586,433 条目录，名称解析正常；删除 `mode3_profile.json` 时使用内置默认密钥/profile，不再因显式路径缺失而退出。
- Wwise 映射工具：扫描 4,784 个 Bank，954/1,009 个独立 WEM 找到所属 BGM Bank（1,164 条引用记录），输出 CSV 正常。
- 内容区固定顶栏：1440×1000 与 500×900 下滚动时搜索、路径、排序、视图控制均保持可见；右侧索引栏按顶栏实际高度自动偏移，无重叠；全局滚动条已统一为深色细样式。
- 资源结构检查：`.animgraph/.animation/.animconfig/.trackgroup/.lod/.spr` 直接显示文本；`.gim/.mtg/.sfx/.efx/.mesh/.skeleton/.rawanimation/.csb/.qm` 提取可读字段、引用路径和 Hex；`.gmp/.xn` 显示格式与 Hex。抽样 16 类资源全部返回结构化结果。

## 数据文件

- `data/pathdict.txt.gz`：542,346 条逻辑路径，SHA-256 `766e315eef7ae9f3b265ca3b003cb954b801e7104b2eee66e7f5624539a8ec76`。
- `mode3_tables.py` SHA-256：`795cee1a2d09ea3abf8c4f6cc8f24af4e25e4d9936d011147aa1703930124e77`。
- `data/mode3_profile.json` SHA-256：`e05e3378110cdabc0107f8d322e03fba44bbea14fb7c073298de0eea814f5256`。

## 说明

该工具包的运行时不需要系统 Python、pip 或 ffmpeg。它只读取用户指定的游戏目录；不包含游戏客户端或资源。游戏版本变化时的兼容性策略见 `VERSION_COMPATIBILITY.md`。
