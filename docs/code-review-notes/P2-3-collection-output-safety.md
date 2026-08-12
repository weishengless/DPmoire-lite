# P2-3：零帧收集删除旧数据且非零输出直接覆盖

- 状态：讨论已完成
- 评审日期：2026-07-11
- 结论日期：2026-07-11
- 优先级：P2
- 关联位置：
  - `src/dpmoire_lite/collect.py:21-47`
  - `src/dpmoire_lite/dataset.py:144-145`
  - `src/dpmoire_lite/manifest.py:25-28`
  - `tests/test_collect.py:159-177`

## 最终问题表述

`run_collect()` 在本次收集到 0 帧时，会直接删除已经存在的正式数据集：

```python
if dataset.n_configs > 0:
    dataset.save_extxyz(output_path)
else:
    if output_path.exists():
        output_path.unlink()
```

一次临时文件系统不可用、集群结果尚未复制、manifest 路径错误或解析器失败，
都可能删除上一次成功生成的 `MD_data.extxyz`、`rlx_data.extxyz` 或
`valid.extxyz`。

非零数据也直接写到正式路径。写出中断可能留下截断文件；即使写出成功，
如果本次只覆盖部分来源，也会不可恢复地替换此前更完整的数据集。

## 问题来源

### 零帧被解释为“清空输出”

收集函数把“本次没有获得数据”和“用户要求删除旧数据”混为同一个操作。
基础 collect 命令没有收到破坏性清空指令，不应隐式删除历史成果。

### 正式路径直接写入

`Dataset.save_extxyz(output_path)` 直接打开最终文件。没有临时文件、回读验证
或原子替换，进程异常和磁盘错误都可能损坏正式输出。

### 非零结果没有恢复点

本次收集得到 800 帧不一定优于上一次的 1200 帧。帧数减少可能源于目录
暂时缺失，也可能是用户主动修改采样频率。程序不能仅凭数量拒绝新结果，但
应保留旧版本供恢复和比较。

## 已确认的零帧策略

### 已存在正式数据集

本次收集为 0 帧时：

- 不删除、不截断、不覆盖旧 extxyz；
- 不创建新的空文件；
- manifest 记录本次状态为 `no_data`；
- 记录 `preserved_previous_output: true`；
- 如能安全读取旧文件，记录旧帧数；
- 输出警告并汇总 missing/skipped/partial/failed 原因。

示例：

```yaml
collect:
  status: no_data
  frames: 0
  written: false
  preserved_previous_output: true
  previous_output_frames: 1200
```

### 不存在正式数据集

- 不创建空 extxyz；
- manifest 记录 `written: false`；
- `preserved_previous_output: false`；
- 明确说明没有可发布数据。

### 显式清空

基础 collect 命令不提供自动删除旧数据的语义。如果用户确实需要清空，应
手动删除，或未来设计名称和确认步骤都明确的独立命令。一次零帧结果不能被
当成删除授权。

### 零帧 manifest-only 提交

零帧不会改变正式 extxyz，因此不创建 data candidate 或 data/manifest 双文件
journal。仍需获取同一 stage/output 单写者锁，将 `no_data` manifest
candidate 写出、fsync、回读验证后通过一次 `os.replace()` 原子发布。该步骤
失败时旧 extxyz 和旧 manifest 都保持不变，CLI 按 fatal 返回 1，而不是
错误返回 no_data 3。

## 已确认的非零发布策略

### 临时写出

1. 在正式输出的同一目录创建唯一临时文件；
2. 将完整候选 Dataset 写入临时 extxyz；
3. 写出期间正式文件保持不变；
4. 临时文件必须位于同一文件系统，以便最终使用原子 rename/replace。

### 回读验证

发布前重新读取临时 extxyz 并验证：

- 可完整解析到 EOF；
- 帧数等于内存 Dataset 的接受帧数；
- 每帧原子数、元素、晶胞和位置可读；
- 每帧包含预期的 energy、forces 和 stress；
- 数组形状与原子数一致；
- 不含只写出一部分的尾帧。

验证失败时删除临时文件，正式输出和旧 manifest 保持不变，并返回非零错误。

### 旧数据备份

候选文件验证成功后，如果正式输出已存在，不能先移动正式文件。移动会在
backup rename 与 candidate publish 之间制造正式路径不存在的窗口，破坏
原子发布保证。

正确顺序是：

1. 读取并记录旧帧数和旧输出 hash；
2. 在同一文件系统创建备份临时路径；
3. 优先通过硬链接保留旧 inode；不支持硬链接时复制旧文件；
4. flush/fsync 复制结果并验证备份 hash 等于旧输出 hash；
5. 通过 `os.replace(backup_tmp, backup_final)` 原子完成备份名称发布；
6. 整个备份过程中旧正式文件始终原位可读；
7. 不自动删除更早备份；
8. manifest 记录备份相对路径和 hash。

示例路径：

```text
work/backups/collect/MD_data.20260711-183000.extxyz
```

### 单写者锁

每个 `(stage, final_output)` 在 collect 和恢复期间必须持有独占单写者锁。
锁在读取旧 hash、检查 pending journal 之前获取，直到数据、manifest 和
journal 提交或恢复结束后才释放。

要求：

- 使用进程退出时由操作系统释放的 advisory/exclusive file lock，而不是只靠
  永久 `O_EXCL` sentinel；
- Linux/Unix 和 Windows 通过统一封装提供等价独占语义；
- lock file 可写入 PID、hostname、开始时间、stage 和 transaction ID 用于
  诊断，但元数据本身不代替 OS lock；
- 无法获取锁时立即以 fatal 退出，不能并发读取同一个 previous hash；
- stale metadata 不自动按时间删除；只有成功获得 OS lock 的进程才能执行
  pending journal 恢复或更新元数据；
- 同一 stage/output 的两个 collect 不能同时创建不同 journal 或互相覆盖。

### transaction candidates 与 journal

输出文件与 manifest 是两个文件，单个 `os.replace()` 不能使它们组成跨文件
原子事务。数据 candidate 和 manifest candidate 都必须在 pending journal
之前完整生成、flush/fsync、回读并校验。

manifest candidate 已包含本次最终 collect 状态、transaction ID、data
candidate hash、backup 信息和来源统计。只有它的内容和 hash 已确定后才能
创建 journal。

pending journal 使用临时文件加 `os.replace()` 原子发布，并至少记录：

```yaml
transaction_id: collect-20260711-183000-<uuid>
state: pending
stage: md
final_output: MD_data.extxyz
data_candidate_path: .MD_data.<transaction_id>.candidate
data_candidate_sha256: "..."
manifest_path: md/manifest.yaml
manifest_candidate_path: md/.manifest.<transaction_id>.candidate.yaml
manifest_candidate_sha256: "..."
previous_output_sha256: "..." # first publish: null
previous_manifest_sha256: "..."
backup_path: backups/collect/MD_data.<transaction_id>.extxyz # first publish: null
backup_sha256: "..." # first publish: null
```

journal 必须在最终输出发生变化前存在。

### 原子替换和提交

发布顺序固定为：

1. 获取 `(stage, output)` 独占锁；
2. 按确定性状态表恢复已有 pending transaction；存在未解决 fatal 状态时停止；
3. 写出、fsync、回读并验证 data candidate，计算 SHA-256；
4. 若旧输出存在，创建、fsync 并原子发布 backup，验证 hash；旧正式文件仍
   原位；首次发布则 previous output hash、backup path/hash 均为 null；
5. 使用已确定的 data/backup 信息构造、fsync、回读并验证 manifest
   candidate，计算 SHA-256；
6. 原子写出并 fsync pending journal，其中记录两个 candidates 及其 hash；
7. 执行 `os.replace(data_candidate, final_output)`；
8. 验证 final output hash 等于 journal 的 data candidate hash；
9. 执行 `os.replace(manifest_candidate, manifest)`；
10. 验证 manifest hash 等于 journal 的 manifest candidate hash，且内部
    transaction ID/data hash 与 journal 一致；
11. 将 journal 原子更新为 committed，随后删除 committed journal；
12. 释放独占锁。

不能通过复制 candidate 到正式路径实现，以免暴露半写文件。

### 确定性中断恢复状态表

恢复只在持有单写者锁时执行。`P` 表示 previous hash，`C` 表示 candidate
hash，`Ø` 表示首次发布时文件应不存在，`X` 表示其他内容。

| Final output | Manifest | Candidate 状态 | 确定动作 |
| --- | --- | --- | --- |
| `P`（首次为 `Ø`） | previous manifest | data 与 manifest candidates 均存在且 hash 正确 | 依次替换 data、验证，再替换 manifest、验证并提交 journal |
| `C` | previous manifest | manifest candidate 存在且 hash 正确 | 不再替换 data；只替换 manifest、验证并提交 journal |
| `C` | candidate manifest | candidates 可存在或已被 replace 消耗 | 验证两者 hash/transaction ID，标记 committed 并清理 journal |
| `P`/`Ø` | candidate manifest | 任意 | 不可能顺序，fatal；不得猜测回滚或继续 |
| `X` | 任意 | 任意 | fatal，外部修改或无法证明状态 |
| 任意 | `X` | 任意 | fatal，manifest 外部修改或损坏 |
| `P`/`Ø` | previous manifest | 任一所需 candidate 缺失或 hash 错误 | fatal；旧正式状态仍保留，不生成新结果 |
| `C` | previous manifest | manifest candidate 缺失或 hash 错误 | fatal；保留 journal 和 backup，要求人工恢复 |

对非首次发布，backup 必须存在且 hash 等于 previous output hash，否则任何
pending 状态都为 fatal。首次发布时
`previous_output_sha256 = backup_path = backup_sha256 = null`，恢复以“正式
输出不存在”作为 `Ø`，不得要求不存在的 backup。

不得使用“继续或清理”“停止或完成”等可选措辞；同一可观察状态只有一个
规定动作。

### committed journal 残留

进程可能在步骤 11 已原子写入 `state: committed`、但尚未删除 journal 时
崩溃。下次 collect 在持有单写者锁后按唯一规则处理：

- final output hash 等于 journal 的 data candidate hash，manifest 文件 hash
  等于 manifest candidate hash，且 manifest 内 transaction ID/data hash 与
  journal 一致：事务已经完成，只删除 committed journal，然后允许新的
  collect 开始；
- 上述任一 hash、transaction ID 或路径不一致：fatal，保留 journal 和所有
  可用 backup，不自动清理或继续新事务。

测试必须故障注入“committed journal 已落盘、unlink 前崩溃”，并分别覆盖
一致状态自动清理和任一 hash 不一致时 fatal。

这解决“数据已替换但 manifest 写入失败”的可诊断和可恢复问题。

### 覆盖下降警告

以下情况发出醒目警告，但不自动否决用户本次明确执行的非零 collect：

- 新帧数少于旧帧数；
- 成功来源数量下降；
- 出现新的 partial/failed/skipped 来源；
- manifest 目录覆盖范围缩小。

警告中必须包含新旧帧数、来源统计和备份位置。

## manifest 示例

```yaml
collect:
  transaction_id: collect-20260711-183000-<uuid>
  status: degraded
  frames: 800
  written: true
  previous_frames: 1200
  preserved_previous_output: false
  backup: backups/collect/MD_data.20260711-183000.extxyz
  output_sha256: "..."
  previous_sha256: "..."
  sources_attempted: 11
  sources_complete: 8
  sources_partial: 1
  sources_failed: 2
```

manifest 自身使用临时文件和原子替换。transaction ID、hash 和 pending
journal 用于跨文件提交恢复，使异常中断后能够识别正式输出和 manifest
是否属于同一次收集。

## 与 P2-1 的关系

- P2-1 决定单个 ML_ABN/OUTCAR 来源贡献哪些完整帧；
- P2-3 决定汇总后的候选数据集如何安全发布；
- complete 和 partial 来源的接受帧进入候选数据集；
- failed 来源不贡献帧；
- 候选为 0 帧时触发保留旧输出规则；
- 候选非零时必须通过临时写出、回读验证、备份和原子替换。

## CLI 状态

本 note 的发布结果按 P2-6 映射退出码：完整发布为 0；有可用帧但存在
partial/failed/skipped 为 2；零帧且保留旧输出为 3；journal、备份或发布
事务无法安全完成为 1。

## 后续实现的验收条件

以下仅记录未来实现要求，本 note 不包含代码修改：

- 零帧收集永远不删除已有 extxyz；
- 零帧且无旧文件时不创建空 extxyz；
- 非零候选只能写到临时路径；
- 临时 extxyz 回读验证失败时旧文件保持字节不变；
- 回读帧数与接受帧数不一致时不发布；
- 每次替换已有正式输出前通过硬链接或复制创建并验证备份；
- 备份期间正式输出路径始终存在；
- 新数据通过验证后使用原子 replace 发布；
- 新帧数或来源覆盖下降时产生包含备份路径的警告；
- manifest 记录新旧帧数、来源状态和备份路径；
- manifest 写出本身是原子的；
- pending journal 在输出替换前原子发布；
- data 和 manifest candidates 都在 journal 前写出、fsync、校验并记录 hash；
- 同一 stage/output 的并发 collect 由 OS 级独占锁阻止；
- 输出和 manifest 记录相同 transaction ID 与 hash；
- 数据已替换但 manifest 未提交时可由 journal 完成恢复；
- 首次发布的 null previous/backup 状态具有测试覆盖；
- committed journal 在 hash/transaction 一致时确定性清理，不一致时 fatal；
- 故障注入覆盖 committed journal 写入后、删除前崩溃；
- 进程在临时写出、验证、备份或替换任一步失败时都有可诊断、可恢复状态；
- 测试覆盖零帧保留、首次零帧、写出失败、回读失败、备份失败、原子替换、
  覆盖下降和 manifest 写出失败。
