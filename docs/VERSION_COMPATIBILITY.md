# 版本兼容性与更新指南

本工具包不是“永远适用于所有版本”的黑盒。NPK 容器中的 **mode 3 索引密钥和密码表来自具体客户端构建**；游戏更新后它们可能变化。工具采用“先验证、再解包”的方式，密钥/格式不匹配时会明确报错，不会静默写出错误资源。

## 已验证构建

当前 profile（`data/mode3_profile.json`）：

```text
profile          pc-1.0.32-engine-1791619829-a50-5119
engine_ver       1791619829
package_version  1.0.32
a50_version      2026-10-14 19:00:30:5119
res_rev          4369854
```

本机 11 个 NPK 全部通过：586,433 条记录、0 个索引错误、192,618 个 PNG 和 1,009 个 FLAC 校验通过。`known_packages` 中记录了这些包的条目数/索引偏移，可作为版本指纹。

## 版本稳定与版本相关的部分

通常稳定，但仍需探测：

* NPK 头部 `NXPK`、24 字节结构、索引偏移。
* 28 字节记录布局（路径哈希、偏移、压缩/原始长度、两个 MurmurHash3、压缩/加密 flags）。
* `flags & 0x100` 表示偏移的第 32 位（>4 GiB 文件）。
* Zstandard 数据帧；`compression=0` 原样、`3` Zstd。
* 路径哈希：MurmurHash3 x86_32，反斜杠路径，默认 seed `0x9747B28C`。
* 逻辑路径清单的格式；但清单内容会随版本增加。

**版本相关，更新后必须重新验证：**

* mode 3 密钥（当前 `c3b034862f0dd214cbace3e37b6175e7`）。
* 自定义 S-box / T-table（`mode3_tables.py`）。
* 路径哈希 seed（profile 的 `path_hash_seed`）。
* 条目/压缩/flags 语义（若客户端格式升级）。
* 路径词典内容（新资源会落到 `_unresolved`）。

## 工具的安全设计

1. **索引验证**：解包前对每个 NPK 检查条目排序、偏移边界、压缩类型、采样 Zstd 帧头；失败抛出 `Mode3FormatError` 并中止，不创建/覆盖输出。
2. **Profile**：`data/mode3_profile.json` 保存密钥、路径 seed、已知包指纹。可用 `--profile` 指定其他 profile。
3. **密钥覆盖**：`--key-hex <32位hex>` 可临时使用新密钥，无需改代码。
4. **密码表覆盖**：`--tables <mode3_tables.py>` 或 profile 的 `tables_file` 可加载新表。
5. **Probe**：`run_probe.bat <游戏目录>` 只读检查所有 NPK，报告条目数、排序、Zstd 帧采样、路径命中率和已知包指纹，不写文件。
6. **Self-check**：`run_selfcheck.bat` 用内置测试向量检查密钥调度、分组解密、路径哈希、Python 依赖和 vgmstream。

## 游戏更新后的处理流程

1. **先探测**

   ```powershell
   run_probe.bat "<party_pc path>"
   ```

   > 上述 <party_pc path> 指的是蛋仔派对PC客户端的安装目录，在所有doc中亦如此

   - 所有包 `FAIL` / 抛出 `NPK index validation failed`：密钥或密码表已变化，**不要解包**。
   - 探测通过但 `known=CHANGED`：包版本变化，可能只是新增资源；可以解包，路径未收录的条目会进入 `_unresolved`。
   - 脚本包 `paths=0/32` 是正常现象（独立脚本命名空间），不代表探测失败。

2. **仅密钥变化（最常见）**

   取得新客户端 Getter A 的 16 字节运行时值后：

   ```powershell
   run_extract.bat "<party_pc path>" "D:\EggyExport" --key-hex <新密钥32位hex>
   ```

   或写入新 profile：

   ```json
   {
     "name": "pc-new-version",
     "key_hex": "........",
     "path_hash_seed": "0x9747B28C",
     "known_packages": {}
   }
   ```

   再用 `--profile 新profile.json` 运行。

3. **密码表也变化**

   `mode3_tables.py` 是从客户端实现提取的固定数据。需要根据新客户端重新生成表（E0–E3、T0–T3、SBOX、INV_SBOX），生成新的表模块并替换/通过 `--tables` 指定。仅靠密钥无法适配不同 S-box/T-table。

4. **路径 seed / 哈希算法变化**

   修改 profile 的 `path_hash_seed`。若哈希算法本身改变（不再是 Murmur3 x86_32 反斜杠路径），需要更新代码并重新验证路径匹配率；不要用旧算法强行改名。

5. **更新测试向量**

   更换密钥/表后，`fixtures/mode3_vectors.json` 和 `fixtures/index_sample.bin` 是旧版本的；`run_selfcheck.bat` 会失败。这是有意的保护。更新 profile 时请同时更新测试向量，或把 selfcheck 理解为“当前 profile 的完整性检查”。

6. **路径词典**

   官方 path list 更新后，替换 `data/pathdict.txt.gz`（每行一个逻辑路径，UTF-8，正斜杠）。不必重新加密，工具会重新计算哈希。未收录的条目始终会落到 `_unresolved/<package>/<signature>.*`。也可以运行 `run_rebuild_data.bat "<游戏目录>"`，从当前客户端的 `ppk/` 分片重新提取路径清单；浏览器在 `data/pathdict.txt.gz` 缺失时会自动尝试同样的重建流程。

## 不会自动完成的事情

* 不会从网络自动获取新密钥、新密码表或新路径清单。
* 不会在密钥不匹配时“猜”明文或覆盖已有输出。
* 不会把 cubemap/数组纹理强行展平成单张 PNG；这些保留原文件并列入 `_fallback` 记录。
* 不会恢复 `script.npk` 的真实脚本文件名；它使用独立命名空间，本地缺少脚本路径表。

## 兼容性结论

* **同版本、同渠道**：开箱即用。
* **小版本更新且只有密钥变化**：`--key-hex`/profile 即可继续使用。
* **密码表/格式变化**：需要按上面的流程更新表或代码；工具会先失败，不会产出错误文件。
* **只新增资源**：索引仍可用；新路径进入 `_unresolved`，更新路径词典后可重新解析。
