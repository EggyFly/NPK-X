# Eggy Party PC — NXPK mode 3 索引格式与路径恢复（最终）

**状态：已解决并完成全包验证（2026-10-20）**

本文档记录本repo开发者持有的游戏客户端当前 NPK 的真实 mode 3 结构、密钥推导、28 字节记录、逻辑路径哈希，以及覆盖全部 11 个 NPK 的验证结果。此前交接文档中“mode 3 明文索引未知 / 路径无法绑定”的条目已被本结果取代。

## 1. 结论摘要

* mode 3 是 **AES-128 形状的逆向分组密码**，使用客户端自定义 S-box / T-table，并非标准 AES-128。
* 每个 NPK 的索引表为 `entry_count * 28` 字节，按 16 字节块解密；**末尾不足 16 字节的部分保持明文**。
* 密钥由 client.exe 的 Getter A 在运行时产生：`c3b034862f0dd214cbace3e37b6175e7`。
* 28 字节记录包含：逻辑路径哈希、物理偏移、压缩/解压长度、MurmurHash3 校验、压缩标志和 flags。
* 路径哈希为 **MurmurHash3 x86_32(path, seed=0x9747B28C)**，路径使用反斜杠形式，例如 `props\ui_model\...\x.mesh`。
* 全量验证结果：**11/11 个 NPK 通过**，共 **586,433** 条记录；**586,419** 条非空载荷全部解压/校验成功，**0** 失败；数据区无重叠，所有间隙均为 0–3 字节零填充。
* 资产类包共 537,562 条记录，其中 **536,788 条（99.84%）**恢复出完整逻辑路径；剩余 774 条是本地路径词典快照未收录的新资源。另有 5 条脚本签名与资产词典随机碰撞，不计入有效路径。
* `script.npk` / `script_patch.npk` 使用独立的脚本命名空间（raw `XN` 容器），其索引与载荷均已验证，但本地没有脚本路径表，故 48,866 条脚本路径名称仍待补（详见 §8）。

## 2. 头部（24 字节，小端）

| 偏移 | 类型 | 含义 |
| ---: | --- | --- |
| 0x00 | char[4] | `NXPK` |
| 0x04 | u32 | 条目数 `entry_count` |
| 0x08 | u32 | `index_offset_high` |
| 0x0c | u32 | 索引模式 `index_mode`（本机全部为 3） |
| 0x10 | u32 | `hash_mode`（本机 0 或 1） |
| 0x14 | u32 | `index_offset_low` |

```
index_offset = (index_offset_high << 32) | index_offset_low
table_end    = index_offset + entry_count * 28
```

本机 11 个 NPK 均满足 `table_end == file_size`（无表尾附加区）。`gui2.npk` 的索引位于 4 GiB 以上，`index_offset_high = 1`，是 64 位索引偏移的实测样本。

## 3. 索引记录（28 字节，`<6I2H`）

| 偏移 | 类型 | 含义 |
| ---: | --- | --- |
| 0x00 | u32 | `signature`：逻辑路径的 MurmurHash3 x86_32 |
| 0x04 | u32 | `offset_low` |
| 0x08 | u32 | `packed_size`（压缩后字节数） |
| 0x0c | u32 | `unpacked_size` |
| 0x10 | u32 | `packed_hash`：压缩数据的 MurmurHash3 x86_32 |
| 0x14 | u32 | `unpacked_hash`：解压数据的 MurmurHash3 x86_32 |
| 0x18 | u16 | `compression`：0 = 原样存储，3 = Zstandard |
| 0x1a | u16 | flags |

**flags** 的关键位：

* `0x100`：`offset` 的第 32 位。`gui2.npk` 共 20,699 条记录带此位，实际偏移为 `offset_low | 0x100000000`。这不是加密标志。
* 本机观测到的 flags 只有 `0x0000` 与 `0x0100`；其余位为 0。

条目按 `signature` 升序排列，客户端在 `0x141180740` 以 `signature` 做二分查找。

### 校验哈希

* `packed_hash = murmur3_x86_32(压缩字节, seed=0x9747B28C)`
* `unpacked_hash = murmur3_x86_32(解压字节, seed=0x9747B28C)`
* `compression == 0` 时两者相等，且 `packed_size == unpacked_size`。

## 4. 逻辑路径

```
signature = murmur3_x86_32(path.replace('/', '\\'), seed = 0x9747B28C)
```

实测示例：

```
props/ui_model/chaoliujiequ_model/chaoliujiequ_tree09/chaoliujiequ_tree09.mesh
-> props\ui_model\...\chaoliujiequ_tree09.mesh
-> signature 0x00CF5E67
```

验证强度：

* `props_ui_model.npk`：337/337 条路径命中；全部以 `props/` 开头。
* `char*`、`gui2`、`props*`、`scene`、`wwise` 条目解析出的路径前缀分别严格为 `char/`、`gui2/`、`props/`、`scene_w/`、`wwise/`；`res.npk` 包含 `effect/`、`maps/`、`shader/` 等多个 `res` 内容根。
* 542,346 条路径词典内 **无哈希碰撞**，排除了“命中的是随机碰撞”的解释。

可复现的路径词典为工作区 `_pathdict.txt`（542,346 行），来自 `ppk/00014d45` 的首个 Zstd 帧。词典是某一版本快照，因此仍有 774 条资产记录找不到名称（§8）。

## 5. mode 3 密码

### 5.1 密钥

client.exe `0x142ec53d0`（Getter A）使用两个 ASCII 常量：

```
"14d20d2f8634b0c3"
"e775617be3e3accb"
```

Getter 的十六进制解析器按 8 字节半区反向写回，实际返回的 16 字节为：

```
c3b034862f0dd214cbace3e37b6175e7
```

该值通过两种独立方式确认：

1. 用 Unicorn 离线执行 Getter A（初始化其 std::string 全局对象）得到的返回值；
2. 直接用它解密全部 11 个 NPK 的索引，所有边界、长度、哈希、路径均成立。

PPK / map 分支使用 Getter B（`0x142ec5480`），其返回值为：

```
70b2215f04d6373f3a7c3ebe21a7ef76
```

（本任务只使用 Getter A 解 NPK 索引；Getter B 记录在此供交叉核对。）

### 5.2 密钥调度与分块

* `expand_key`：对 16 字节密钥按 u32 应用 `neo` 变换，然后执行 AES-128 递推（custom S-box 用于 g 函数）。
* `prepare_schedule`（client `0x141185420`）：按 16 字节组倒序轮密钥；对中间 9 组应用客户端的 inverse-MixColumns 组合表；末尾补 16 个零字并在第 60 字写轮数 10。
* `decrypt_block`（client `0x141184840`）：两轮初始 T-table 变换、四组双轮、末轮 inverse S-box；输入和输出均应用 `neo`：

```
neo(v) = ror8(v) ^ ((ror8(v) ^ rol8(v)) & 0x00FF00FF)
```

* 索引解密只处理完整 16 字节块；`len(table) % 16` 的尾部原样保留。例如 `props_ui_model.npk` 的 9,436 字节表最后 12 字节保持明文。

`mode3_tables.py` 内是已验证的自定义 S-box、inverse S-box、E0–E3（调度用）与 T0–T3（分组用）表。它们是从客户端实现中固定提取的数据；纯 Python 解码器不依赖 `client.exe`。

### 5.3 与客户端一致性

用 Unicorn 执行客户端 `0x141185420 + 0x141184840` 的实际代码，与纯 Python 实现对同一密钥、随机分组逐字节一致。固定测试向量：

```
key        = c3b034862f0dd214cbace3e37b6175e7
block 0    = 00000000000000000000000000000000
plain 0    = 323859d1baeddebe4c38332f9bfba392
block 1    = 00112233445566778899aabbccddeeff
plain 1    = ad30648f468aad1441397f41b32d81c2
```

## 6. 全包验证结果

验证脚本：`python validate_npk_mode3.py`  
报告：`nxpk_mode3_validation.json`、`_validation_reports/*.json`

| 文件 | 条目 | 压缩 | 路径已解析/未解析 | 结果 |
| --- | ---: | --- | ---: | --- |
| char.npk | 148,779 | Zstd 148,774 / stored 5 | 148,655 / 124 | valid |
| char_loading.npk | 22,291 | Zstd 22,291 | 22,283 / 8 | valid |
| gui2.npk | 128,551 | Zstd 128,548 / stored 3 | 128,527 / 24 | valid |
| props.npk | 83,980 | Zstd 83,980 | 83,751 / 229 | valid |
| props_ui_model.npk | 337 | Zstd 337 | 337 / 0 | valid |
| props_zhucheng.npk | 338 | Zstd 338 | 338 / 0 | valid |
| res.npk | 118,440 | Zstd 118,380 / stored 60 | 118,409 / 31 | valid |
| scene.npk | 29,053 | Zstd 29,053 | 28,695 / 358 | valid |
| script.npk | 48,751 | stored 48,751 | 0 / 48,751（脚本命名空间；5 条随机碰撞不计） | valid |
| script_patch.npk | 120 | stored 120 | 0 / 120（脚本命名空间） | valid |
| wwise.npk | 5,793 | Zstd 1,009 / stored 4,784 | 5,793 / 0 | valid |
| **合计** | **586,433** | — | **536,788 / 49,645**（另有 5 条脚本随机碰撞） | **11/11 valid** |

验证内容与结果：

* 每条记录偏移/长度均在 `[24, index_offset)` 内；`compression == 3` 的数据起始为 `28 b5 2f fd`，解压长度等于 `unpacked_size`；Zstd 帧恰好在 `packed_size` 处结束。
* `packed_hash` 与 `unpacked_hash` 全部通过（MurmurHash3，seed `0x9747B28C`）。
* 条目按 signature 严格升序；非空条目之间 **0 重叠**。
* 字节账目精确闭合：每个包都满足

```
(index_offset - 24) = Σ packed_size + 所有间隙
```

  所有间隙均为 **0–3 字节零填充**（最大 3 字节），不存在未解释的数据区域或未被引用的帧空间。
* `gui2.npk` 的 20,699 条 `flags=0x100` 全部通过 `offset | 0x100000000` 解出，并与 Zstd 帧、长度、哈希逐项匹配。
* 14 条零长度占位记录（gui2 1 条、res 13 条）不占数据区，单独计为合法空记录。

## 7. 可复现工具

```powershell
# 单包解码/验证（默认读取 _pathdict.txt，可省略 --paths）
python .\nxpk_mode3.py "<party_pc path>\props_ui_model.npk" --paths .\_pathdict.txt

# 全部 11 个 NPK 全包验证（并发 4 进程，约 1–2 分钟）
python .\validate_npk_mode3.py

# 生成全量 path -> 记录清单（gzip CSV）
python .\build_mode3_manifest.py

# 回归测试（包含与客户端模拟结果的固定向量）
python -m unittest test_nxpk_mode3 test_nxpk_clear_fixture test_ktx_to_png
```

交付文件：

* `nxpk_mode3.py`：自包含解码器（头部、索引解密、记录解析、路径哈希、全包审计）。
* `mode3_tables.py`：验证过的密码表（数据，不是密钥）。
* `validate_npk_mode3.py`：11 个 NPK 的全包验证入口。
* `build_mode3_manifest.py`：路径/偏移/长度/哈希清单生成器。
* `nxpk_mode3_validation.json`、`_validation_reports/`：逐包验证报告。
* `nxpk_mode3_manifest.csv.gz`（约 23 MB）：586,433 行的 path → package/offset/size/hash 清单。
* `test_nxpk_mode3.py`：密钥、调度、分组、路径哈希、4 GiB 位和小包全量测试。

## 8. 已知边界

1. **774 条资产路径未解析**（char 124、char_loading 8、gui2 24、props 229、res 31、scene 358）。这些条目的索引、帧、长度、哈希均已验证；缺失的是 `_pathdict.txt` 快照中没有对应名称的新资源。没有发现可替代的规范化/扩展名变体（大小写、斜杠、`.png`/`.kta` 等均已试验）。
2. **脚本路径名称未恢复**：`script.npk`（48,751 条）与 `script_patch.npk`（120 条）是 raw `XN` 容器，签名不属于资产路径命名空间。资产词典对脚本签名仅有 5 次命中；按 48,751 × 542,346 / 2^32 ≈ 6 次计算，这 5 条是随机碰撞，不能当作脚本路径。脚本路径表（客户端版本信息中的 `script_file_list` / `nxs_path_hash_info`）当前不在本机；记录与载荷本身已 100% 验证。
3. 索引密码实现针对本机 2026 PC 构建。换版本/换渠道包时，应先验证 Getter A 返回值或重新执行同样的全包校验，不要把本文档的密钥当作跨版本常量。

## 9. 关键静态地址（供复核）

| 地址 | 作用 |
| --- | --- |
| `0x141180400` | NPK 打开/头部读取；`0x141180542` 检查 `NXPK` |
| `0x1411806b3` | 读取 `index_mode` 并调用模式分发器 |
| `0x141180740` | 按 signature 二分查找记录 |
| `0x14117dcb0` | 按模式分发（表 `0x147561c00`，mode 3 → `0x14117d540`） |
| `0x14117d540` | mode 3：`0x141185420` 生成调度，`0x141184840` 解密 16 字节块 |
| `0x141185690` | 密钥扩展（含 `neo` 与自定义 S-box） |
| `0x142ec53d0` | Getter A（NPK 索引密钥） |
| `0x142ec5480` | Getter B（PPK / map 分支） |
| `0x142ec55b0` | 十六进制密钥解析器（8 字节半区反向） |
| `0x143677ab0` | 两个 8 字节常量拼接包装 |

所有结论均来自只读离线分析；解码器不启动、不附加、不修改客户端。
