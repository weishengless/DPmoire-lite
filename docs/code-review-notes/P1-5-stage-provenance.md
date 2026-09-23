# P1-5：Stage1 未验证 Stage0 的结构来源

- 状态：讨论已完成
- 评审日期：2026-07-11
- 结论日期：2026-07-11
- 优先级：P1
- 关联位置：
  - `src/dpmoire_lite/build.py:73-101`
  - `src/dpmoire_lite/build.py:198-219`
  - `src/dpmoire_lite/build.py:335-350`
  - `src/dpmoire_lite/build.py:448-466`
  - `src/dpmoire_lite/manifest.py:10-37`

## 最终问题表述

Stage1 使用当前 config 的 `sc_rlx` 判断弛豫 CONTCAR 是 primitive structure
还是已经扩胞的 structure：

```python
_write_md_poscar(
    source_dir / "CONTCAR",
    target / "POSCAR",
    config.sc if not config.sc_rlx else None,
)
```

但 Stage0 的 relaxation manifest 没有记录 `sc_rlx`，Stage1 也不验证当前
config 是否仍与生成弛豫目录时一致。用户分阶段手动运行时，如果在 Stage0
和 Stage1 之间误改 `sc_rlx`，程序会静默重复扩胞或漏掉必要扩胞。

该问题与 Slurm 自动化无关。即使团队只使用 DPmoire-lite 准备目录、手动在
不同集群提交，也会影响 Stage1 生成结构的科学正确性。

## 最小复现

1. Stage0 使用：

   ```yaml
   sc_rlx: true
   sc: [2, 1]
   ```

2. 弛豫 POSCAR 和最终 CONTCAR 已经是 4 原子超胞。
3. 运行 Stage1 前改为：

   ```yaml
   sc_rlx: false
   sc: [2, 1]
   ```

4. Stage1 再次扩胞，生成 8 原子的 MD POSCAR。

反向修改会把本应扩胞的 primitive CONTCAR 直接用作 MD POSCAR。

## 问题来源

### manifest 缺少决定结构解释的字段

当前 `_config_summary()` 记录了 `n_sectors`、`sc`、`d` 等字段，但没有记录
`sc_rlx`。manifest 也没有 schema 版本和明确的 structure provenance。

### Stage1 信任当前 config

Stage1 从 relaxation manifest 读取 stacking 列表，但决定是否扩胞时仍使用
当前 config。manifest 与 config 因而具有两个互相独立的事实来源。

### 输入结构和 Stage0 POSCAR 没有身份记录

manifest 没有记录上下层输入结构哈希，也没有记录各个 Stage0 `rlx/POSCAR`
的原子数、元素组成、晶胞或哈希。程序不能判断 Stage1 是否混用了旧弛豫
结果和后来更换的 input。

## 已确认的总体策略

采用“双轨 provenance”方案：

- 新版本生成的 Stage0：manifest 显式记录完整结构来源，Stage1 严格校验；
- 缺少 provenance 的旧 manifest：通过原子数和晶胞两个独立判据安全推断；
- 旧计算不追溯要求具有当时不存在的哈希，不需要因此重新运行 init_mlff
  或 relaxation；
- 所有校验都必须在备份或创建 MD 目录之前完成。

## 新格式 manifest

### schema 与结构来源

新 manifest 应具有明确的 schema/version 字段，并在 structure provenance
中记录：

- `sc_rlx`；
- `n_sectors`；
- `symm_reduce`；
- `d`；
- `d_mode`；
- `d_reference`；
- Stage0 的 `sc`；
- 实际 stacking 列表；
- `input/top_layer.poscar` 的内容哈希；
- `input/bot_layer.poscar` 的内容哈希；
- 每个 `rlx/<stacking>/POSCAR` 的内容哈希、原子数、元素组成和晶胞。

文件哈希用于确认字节级输入身份；原子数、元素组成和晶胞用于生成可诊断的
结构差异，而不只是给出一个无法解释的 hash mismatch。

### 不可跨阶段改变的结构来源

以下内容在 Stage0 和 Stage1 间必须一致：

- 上下层 POSCAR 内容；
- `n_sectors`；
- `symm_reduce`；
- `d`；
- `d_mode`；
- `d_reference`；
- `sc_rlx`；
- Stage0 实际 stacking 列表；
- `sc`，仅当 Stage0 使用 `sc_rlx: true` 时。

当 `sc_rlx: true` 时，relaxation POSCAR 已经使用 `sc` 扩胞，所以 Stage1
不能把另一个 `sc` 与旧结构混用。

### 允许 Stage1 调整的设置

以下设置不改变 Stage0 弛豫结构的来源，允许按 MD 需要修改：

- `stage`；
- `submit` 和 `wait`；
- `vasp_ml`；
- `k_mesh`；
- `r_cut`；
- `potcar_policy`；
- `include_monolayer_md`；
- `MD_INCAR` 和 `MD_monolayer_INCAR`；
- `sc`，仅当 Stage0 使用 `sc_rlx: false` 时。

`sc_rlx: false` 表示 Stage0 弛豫 primitive structure，`sc` 本来就是 Stage1
的 MD 扩胞参数，因此可以在 Stage1 有意调整。

### Workflow cutoff

`encut` 属于 Stage0 与 Stage1 的截断身份，不在上面的可调整列表里。Stage1
把当前计划与 relaxation manifest 的 `workflow_cutoff` 比较：

- `dpmoire-lite.workflow-cutoff.v2` 必须整份记录一致；
- `dpmoire-lite.workflow-cutoff.v1` 在 `selected_potcars` 与 `encut` 一致时接受，已退役的 `governing_element`、`governing_potcar_directory`、`max_enmax` 和 `encut_factor` 不参与比较；
- 没有 `workflow_cutoff` 的历史 manifest 只发出警告，并使用当前新解析的计划；
- `encut` 或所选 POTCAR 不同，以及其他 schema，都在写出 MD 目录之前失败。

### 新格式的 Stage1 判定

1. 读取 relaxation manifest 的 structure provenance；
2. 验证不可变字段和输入文件哈希；
3. 验证实际 `rlx/POSCAR` 与 manifest 记录一致；
4. 验证 CONTCAR 可读，原子数和元素组成与对应 Stage0 POSCAR 一致；
5. 使用 manifest 的 `sc_rlx` 解释 CONTCAR；
6. 当前 config 与来源语义冲突时停止，而不是静默选择其中一个；
7. 全部检查通过后才能备份或创建 MD 目录。

## 旧 manifest 兼容路径

### 目标

已经完成 init_mlff 和 relaxation 的旧计算，在升级后仍应能直接运行 Stage1。
不能要求用户因为新增 provenance 字段而重做昂贵计算。

### 推断输入

对于缺少 structure provenance 的旧 manifest，Stage1 使用：

- 旧 manifest 中的实际 stacking 列表；
- 每个 `rlx/<stacking>/POSCAR`；
- 每个 `rlx/<stacking>/CONTCAR`；
- 当前 `input/top_layer.poscar`；
- 当前 `input/bot_layer.poscar`。

`rlx/POSCAR` 是 Stage0 当时真正提交给 VASP 的结构，应作为主要证据；不能
只看 CONTCAR 或当前 config。

### 第一判据：原子数和元素组成

使用当前上下层 input 重建对应 primitive bilayer：

- `rlx/POSCAR` 原子数等于 primitive 原子数，且元素组成一致：候选
  `sc_rlx: false`；
- 原子数是 primitive 的整数倍，且元素组成按相同比例复制：候选
  `sc_rlx: true`；
- 不能得到整数一致关系时停止。

### 第二判据：面内晶胞关系

- `rlx/POSCAR` 面内晶胞与 primitive 一致：确认 `sc_rlx: false`；
- 晶胞可表示为项目支持的整数超胞变换，并且变换行列式与原子数倍率一致：
  确认 `sc_rlx: true`，同时推断当时的 `sc`；
- `[2, 1]` 和 `[1, 2]` 虽然原子数倍率相同，但晶胞方向不同，必须由第二
  判据区分；
- 原子数和晶胞结论冲突时停止。

### CONTCAR 交叉检查

- CONTCAR 的原子数和元素组成必须与对应 Stage0 POSCAR 一致；
- 示例 `rlx_INCAR` 使用 `ISIF = 2`，通常还应保持晶胞一致；
- 若用户使用允许变胞的弛豫设置，可允许数值晶胞变化，但不能破坏已经推断
  的原子数和超胞拓扑；
- 所有 stacking 目录必须得到同一个 `sc_rlx` 结论；超胞路径还必须得到
  相容的 `sc`。

### 旧格式处理结果

#### 推断唯一且与当前 config 一致

允许继续，发出一次 legacy provenance 警告，并把推断结果和证据写入新的
MD manifest；不反向伪造或覆盖旧 relaxation manifest。

示例：当前测试目录中上下层各 4 个原子，primitive bilayer 为 8 个原子，
`rlx/0_0/POSCAR` 也是 8 个原子且面内晶胞一致，因此可以唯一推断：

```yaml
sc_rlx: false
```

当前 config 同样为 `sc_rlx: false`、`sc: 3`，所以 Stage1 可以继续把 8
原子扩为 72 原子的 MD structure，无需重做 relaxation。

#### 推断唯一但与当前 config 冲突

停止并报告推断结果与当前设置，例如：

```text
Legacy relaxation folders were inferred as:
  sc_rlx: true
  sc: [2, 1]

Current config contains:
  sc_rlx: false
  sc: [2, 1]

Correct config.yaml and rerun stage1.
No MD directories were modified.
```

程序不能悄悄忽略当前 config，也不能自动改写用户配置。

#### 不能唯一推断

以下情况停止，不继续猜测：

- 缺少 `rlx/POSCAR` 或 CONTCAR；
- 当前 input 与旧 Stage0 POSCAR 的原子组成或晶胞无法对应；
- 原子数与晶胞判据冲突；
- 不同 stacking 得到不同结论；
- 超胞矩阵不能可靠恢复；
- 文件不可由 ASE 读取。

错误信息应说明缺少的证据以及用户可恢复的原始 input 或文件。

## stacking 来源规则

- Stage1 必须存在 relaxation manifest，并使用其中的实际 stacking 列表；
- 不根据当前 `n_sectors` 或 `symm_reduce` 重新生成另一组目录；
- 删除从 `sym_reduced_stackings.txt` 和 config 推导 stacking 的 fallback；
- manifest 完全缺失时直接报错，不进入 legacy inference；
- 新格式 manifest 中 stacking 列表与结构来源字段不一致时停止；
- 旧格式兼容仅指 manifest 存在但缺少新版 provenance 字段；它仍以 manifest
  的实际目录列表为主，双判据只判断结构是否已扩胞。

## 后续实现的验收条件

以下仅记录未来实现要求，本 note 不包含代码修改：

- 新 Stage0 manifest 记录 schema version 和完整 structure provenance；
- Stage1 使用 manifest 的 `sc_rlx`，不单独信任当前 config；
- `sc_rlx` 跨阶段变化在任何 MD 目录变更前报错；
- `sc_rlx: true` 时，`sc` 变化报错；
- `sc_rlx: false` 时，Stage1 可有意修改 MD `sc`；
- 上下层结构或 Stage0 POSCAR 被修改时，新格式 provenance 能给出具体差异；
- 旧 manifest 在原子数与晶胞判据一致时仍可继续 Stage1；
- 当前 `example-test` 旧目录能够推断为 `sc_rlx: false` 并继续生成 72 原子
  MD structure；
- 旧格式推断冲突或证据不足时停止且不修改 MD 目录；
- relaxation manifest 缺失时停止，不从 config 或辅助文件推导；
- 所有 stacking 必须得到一致来源结论；
- MD manifest 记录使用的是严格 provenance 还是 legacy inference，以及相关
  证据；
- 测试覆盖重复扩胞、漏扩胞、同 determinant 不同方向超胞、旧格式成功
  推断、旧格式冲突和输入文件变化。
