# P2-1：截断来源的部分帧与 MLFF seed 前缀处理

- 状态：讨论已完成
- 评审日期：2026-07-11
- 结论日期：2026-07-11
- 优先级：P2
- 关联位置：
  - `src/dpmoire_lite/dataset.py:17-22`
  - `src/dpmoire_lite/dataset.py:46-111`
  - `src/dpmoire_lite/collect.py:67-97`
  - `src/dpmoire_lite/inputs.py:233-237`
  - `tests/test_collect.py:77-155`

## 最终问题表述

`Dataset.load_ml_ab()` 将每个解析成功的 configuration 立即追加到共享总
数据集。如果同一个 ML_ABN 的后续 configuration 损坏，collector 会把整个
来源记录为 `failed`，但之前追加的帧仍留在最终 extxyz 中。

这会造成 manifest 与实际数据不一致：manifest 声称来源失败，数据集却包含
该来源的部分帧。

然而，团队实际在受 walltime 限制的集群上运行。VASP 可能在写最后一个
configuration 时被终止，导致 ML_ABN 只在文件尾部截断。此前已经完整写出
的 configuration 仍有收集价值，全部丢弃会浪费昂贵计算。

因此最终策略不是简单的“单源全有或全无”，而是区分可安全抢救的 EOF
尾部截断与文件内部损坏。

## 问题来源

当前 `load_ml_ab()` 在解析循环内部执行：

```python
self.data.append(atoms)
self.n_configs += 1
```

异常由 `_collect_md_ml()` 外层捕获。外层只能记录整个文件解析失败，无法回滚
已经追加的帧，也无法说明接受了多少完整 configuration。

当前 manifest 只有 `skipped` 和 `failed`，没有 `partial` 状态。收集汇总只
记录总 `sources` 和 `frames`，不能区分完整来源、部分来源和失败来源。

## 已确认的截断抢救规则

### 完整来源

- 所有目标 configuration 解析成功；
- 全部加入数据集；
- 来源状态为 `complete`。

### EOF 尾部截断

如果最后一个 configuration 因文件结束而不完整：

- 保留此前所有完整 configuration；
- 丢弃不完整的最后一帧；
- 来源状态为 `partial`，不记为普通 `failed`；
- 记录被丢弃的 configuration 编号和具体缺失区块；
- 输出一次警告。

这适用于 walltime 到期、作业被正常终止或文件复制时只保留到某个尾部位置
的常见场景。

### 第一帧即不完整

- 来源贡献 0 帧；
- 状态为 `failed`；
- 记录具体解析位置。

### 文件内部损坏

如果某个损坏 configuration 后仍存在后续 configuration 或明显的有效结构，
则不是普通 EOF 尾部截断：

- 不自动把它解释为 walltime 截断；
- 该来源按内部损坏处理；
- 默认不抢救其前缀，避免掩盖拼接、传输或格式错误；
- manifest 记录冲突位置供用户检查。

### OUTCAR

未来 OUTCAR 改为流式读取时采用同一原则：只抢救 EOF 前完整解析的 ionic
steps；中间损坏不自动跳过。当前 `read_vasp_out(path, ":")` 会整体读取，
实现流式尾部抢救需要后续调整解析接口。

## MLFF seed 前缀

### 工作流契约

DPmoire-lite 支持的 MLFF 流程是：

```text
init_mlff/ML_ABN
    -> Stage1 copies to md/<stacking>/ML_AB
    -> VASP continues training and writes md/<stacking>/ML_ABN
```

VASP 生成的 MD ML_ABN 以输入 ML_AB 数据库为基础，因此其前缀包含 seed
configurations。收集 MD 新数据时必须跳过这部分，避免每个 stacking 都重复
加入 init_mlff 数据。

### 当前行为

当前 collector 读取 `md/<stacking>/ML_AB` 第 5 行声明的 configuration
数量，并从 ML_ABN 开头跳过相同数量。它已经避免正常工作流中的 seed
重复，但只依赖本地文件中的数量，没有记录 Stage1 分发身份。

实际测试目录中：

```text
init_mlff/ML_AB   : 78 configurations
init_mlff/ML_ABN  : 110 configurations
md/0_0/ML_AB      : 110 configurations
```

Stage1 将 `init_mlff/ML_ABN` 复制成 MD 的 `ML_AB`，所以该 MD 后续收集时
应跳过 110 个 seed configurations。

### MD 续算暴露的当前缺陷

MD 续算通常执行：

```text
cp ML_ABN ML_AB
```

因此当前 MD 目录中的 `ML_AB` 会随续算增长，不再等于 Stage1 最初分发的
init_mlff seed。例如：

```text
Stage1 initial seed       : 110
first MD output ML_ABN    : 140
restart input ML_AB       : 140
second MD output ML_ABN   : 170
```

最终 ML_ABN 中属于所有 MD 运行的新数据共有 `170 - 110 = 60` 帧。当前代码
读取续算后的 `md/ML_AB` 数量 140，会只收集 `170 - 140 = 30` 帧，从而丢失
第一次 MD 已经产生的 30 帧。

所以“需要跳过的 seed 数量”必须是 Stage1 首次从 init_mlff 分发时记录的
不可变值，不能在 collect 时从当前 MD/ML_AB 重新推断。

## 已确认的结构化 seed-prefix 方案

初次复制文件 hash 只能证明 Stage1 分发时 `ML_AB` 正确，不能证明多次
restart 后最终 ML_ABN 的前 N 帧仍是同一 initial seed。收集前必须对 seed
prefix 做结构化校验；仍不进行不同 MD 新帧之间的模糊结构去重。

### Stage1 分发

1. 检查 `init_mlff/ML_ABN` 和 `ML_FFN` 存在且非空；
2. 完整解析 initial seed，读取 configuration 数量；
3. 按下述 canonical schema 计算 `seed_prefix_digest`；
4. 分别复制为每个 MD 目录的 `ML_AB` 和 `ML_FF`；
5. 复制后验证文件大小和原始文件 SHA-256；
6. 在 MD manifest 中记录：

   ```yaml
   mlff_seed:
     source: init_mlff/ML_ABN
     configurations: 110
     digest_schema: mlab-seed-v1
     seed_prefix_sha256: "..."
     ml_ab_sha256: "..."
     ml_ff_sha256: "..."
   ```

7. 任一目录分发失败时，不把该目录视为可提交的完整 MD 输入目录。

seed 记录表示 Stage1 初始基线。后续用户为 MD restart 执行
`cp ML_ABN ML_AB` 时，当前 MD/ML_AB 可以合法变化，不能反向修改 manifest
中的 initial seed count/hash。

### canonical seed digest

`mlab-seed-v1` 对每个 configuration 按固定字段顺序序列化：

- 有序元素符号和每类原子数；
- 原子总数；
- 3x3 lattice；
- 按文件顺序的 positions；
- total energy；
- 按文件顺序的 forces；
- 规范化为固定分量顺序的 stress tensor。

原始物理单位和符号约定固定为：

- lattice：Å；
- positions：Cartesian Å，若源区块使用其他坐标表达，先转换为 Cartesian Å；
- total energy：eV；
- forces：eV/Å；
- stress：使用 ML_AB/ML_ABN 文件中的原始 VASP stress 数值，单位 kbar，
  固定分量顺序 `[xx, yy, zz, xy, yz, zx]`；
- digest 的 stress 不应用写入 ASE calculator 时的负号或 `kbar -> eV/Å^3`
  转换，避免 ASE 内部约定改变 seed identity。

序列化必须包含数组 shape 和长度前缀；字符串使用 UTF-8；数值解析为
IEEE-754 float64 后使用固定 big-endian bytes；整数使用固定宽度 big-endian。
configuration 编号、空白、注释和浮点文本格式不进入 digest，使等价的文本
重排不会改变科学内容身份。schema 名称必须随 digest 一起写入 manifest，
未来字段或编码变化必须升级 schema，不能静默复用 v1。

所有 float 在序列化前执行：

- `-0.0` 规范化为 `+0.0`；
- 拒绝 NaN、`+Inf` 和 `-Inf`；
- 不进行容差舍入；解析后的有限 float64 bit pattern 必须一致；
- 验证 positions/forces shape 与原子数一致，元素类型计数之和等于原子数。

header configuration count 也是结构不变量：

- Stage1 initial seed 必须完整解析，header 声明数量必须等于完整解析数量；
- 标记为 complete 的最终 ML_ABN 也必须完全相等；
- 标记为 partial 的最终 ML_ABN 必须满足：header 声明数量恰好等于完整解析
  configuration 数量加 1，并且文件尾部恰好存在一个已经开始但未完成的
  `Configuration num.` 区块；该尾块丢弃，完整前缀可抢救；
- header 等于完整解析数量且不存在不完整尾块时属于 complete，不属于
  partial；
- header 等于完整解析数量但仍存在不完整尾块时按格式失败处理，不自动猜测
  VASP 是否尚未更新 header；
- header 小于完整解析数量、或大于“完整数量 + 1”，均为格式失败；
- 其他 count mismatch 均为格式失败，不能只相信 header 第五行。

该关系必须用从真实 ML_ABN 裁剪出的尾部截断 fixture 固化：保留原 header，
在最后一个 configuration 的 position/force/stress 子区块中分别截断，验证
`declared = complete + 1`。如果未来获得自然 walltime 中断文件并证明 VASP
某版本采用不同 header 更新顺序，应新增带 VASP 版本的 fixture 和显式兼容
规则，不能放宽为任意 count mismatch。

Stage1 对完整 initial seed 的 canonical byte stream 计算 SHA-256。原始文件
SHA-256 仍用于证明首次复制字节一致，两种 hash 不能互相替代。

### 收集

1. 新格式 manifest 存在时，读取不可变的 initial seed count、digest schema
   和 `seed_prefix_sha256`；
2. collect 不要求当前 `md/<stacking>/ML_AB` 与 initial seed hash 一致，
   因为正常 restart 会用较新的 ML_ABN 替换 ML_AB；
3. 顺序解析最终 ML_ABN 的前 N 个完整 configurations，并按相同 schema
   计算 prefix digest；
4. digest 与 manifest 不一致时，该来源按 seed provenance failure 处理，
   贡献 0 帧，不能只按 header count 跳过；
5. digest 一致后，无论 restart 多少次，最终 ML_ABN 始终只跳过这 N 个
   Stage1 initial seed configurations；
6. seed 前缀之后的完整新 configuration 正常加入数据集，其中包括此前各次
   restart 已经积累的数据；
7. 新数据区最后一帧截断时执行 EOF 尾部抢救；
8. 如果 ML_ABN 中完整 configuration 总数少于 initial seed 数量，则 seed
   本身未完整写出，来源失败且贡献 0 个新帧；
9. 旧 manifest 没有 seed 信息时，优先完整解析
   `work/init_mlff/ML_ABN`，现场计算 count 和 canonical digest，再复核最终
   ML_ABN prefix；
10. 旧计算的 init_mlff seed 文件缺失时，不能使用可能已经增长的当前
   `md/ML_AB` 静默推断。应要求用户恢复原始 seed 文件，或通过后续设计的
   明确 legacy migration 输入提供 count、schema 和 digest。

### 不进行的去重

- 不在不同 MD 目录之间进行模糊几何去重；
- 不因两个热构型位置接近而删除其中一个；
- 不比较原始文本，但必须结构化验证 final ML_ABN 的 initial seed prefix；
- 不自动按结构相似度改变训练样本权重。

如果未来确实需要全局去重，应作为独立数据策展功能设计，而不是混入基础
collect 流程。

## manifest 记录

建议收集汇总区分：

```yaml
collect:
  sources_attempted: 10
  sources_complete: 8
  sources_partial: 1
  sources_failed: 1
  frames: 1240
```

部分来源示例：

```yaml
partial:
  - path: md/0_1/ML_ABN
    complete_frames: 86
    discarded_configuration: 87
    reason: unexpected EOF in stress block
    file_size: 12345678
```

失败来源仍记录在 `failed`。完整或部分来源实际贡献的帧数必须与最终 extxyz
和 `collect.frames` 一致。

## 后续实现的验收条件

以下仅记录未来实现要求，本 note 不包含代码修改：

- 完整 ML_ABN 全部收集并标记 `complete`；
- 尾部截断只丢弃不完整末帧，完整前缀标记 `partial` 并进入数据集；
- 文件内部损坏不被误判为普通尾部截断；
- 第一帧损坏时贡献 0 帧；
- manifest 中 complete/partial/failed 数量与实际来源一致；
- Stage1 校验并记录 seed count、原始文件 hash 和 canonical prefix digest；
- `mlab-seed-v1` 固定单位、stress 原始符号和 `[xx,yy,zz,xy,yz,zx]` 顺序；
- digest 规范化负零、拒绝 NaN/Inf，并验证数组 shape/type count；
- initial seed header count 等于完整解析数量；
- partial 只允许 `declared_count == complete_count + 1` 且唯一尾块不完整；
- 真实格式截断 fixture 覆盖 position、force 和 stress 尾部；
- 收集器只有在 final ML_ABN prefix digest 一致后才跳过 init_mlff 前缀；
- 多次执行 `cp ML_ABN ML_AB` 续算后仍只跳过 Stage1 initial seed；
- collect 不因当前 MD/ML_AB hash 已随 restart 变化而拒绝合法来源；
- ML_ABN 短于 seed 前缀时不产生新帧；
- 旧 manifest 优先从 `init_mlff/ML_ABN` 重建 count/digest 并给出警告；
- 缺少旧 initial seed 证据时不从当前 MD/ML_AB 猜测；
- 不执行模糊结构去重；
- 最终 extxyz 帧数等于所有 complete 和 partial 来源的已接受帧之和；
- 测试覆盖完整文件、尾部截断、内部损坏、seed-only、短于 seed、多次
  restart、相同 count 不同 prefix、文本格式变化但 canonical 内容相同、旧
  init seed 回退和 initial seed 证据缺失。
