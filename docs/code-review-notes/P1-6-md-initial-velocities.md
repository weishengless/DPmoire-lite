# P1-6：Stage1 将弛豫 CONTCAR 的零速度带入 MD POSCAR

- 状态：讨论已完成
- 发现日期：2026-07-11
- 结论日期：2026-07-11
- 优先级：P1
- 关联位置：
  - `src/dpmoire_lite/structures.py:174-176`
  - `src/dpmoire_lite/build.py:101`
  - `src/dpmoire_lite/build.py:379-387`
  - `src/dpmoire_lite/inputs.py:225-230`
  - `tests/test_build.py:89-97`
  - `tests/test_build.py:291-315`
- 实际样例：`example-test/example-test/1-calculate`

## 最终问题表述

VASP 完成结构弛豫后，生成的 CONTCAR 可能在离子坐标之后包含速度区块。
弛豫产生的速度通常全部为零。Stage1 当前通过 ASE 读取弛豫 CONTCAR，
随后直接重写或扩胞后写成 MD POSCAR。ASE 会读取、传播并重新写出速度，
导致 MD POSCAR 显式携带零速度。

当 POSCAR 已提供初速度时，VASP 使用该速度，而不会根据 `TEBEG` 随机生成
目标温度下的 Maxwell-Boltzmann 初速度。因此新 MD 会从 0 K 开始升温，
而不是从配置的初始温度开始。

Stage1 的语义已经确认是“从弛豫后的稳定结构开始一条新的 MD 轨迹”，不是
“续算旧 MD”。因此所有 Stage1 生成的 MD POSCAR 都必须无条件删除输入
速度。

## 实际样例证据

样例配置：

```yaml
stage: 1
sc_rlx: false
sc: 3
```

源文件：

```text
example-test/example-test/1-calculate/rlx/0_0/CONTCAR
```

该 CONTCAR 包含 8 个原子，坐标后还有 8 行全零速度：

```text
0.00000000E+00  0.00000000E+00  0.00000000E+00
...
```

生成文件：

```text
example-test/example-test/1-calculate/md/0_0/POSCAR
```

`sc: 3` 将 8 个原子扩为 72 个原子。生成的 POSCAR 共 153 行，组成是：

- 8 行 POSCAR 头部；
- 72 行离子坐标；
- 1 行速度坐标模式 `Cartesian`；
- 72 行全零速度。

这证明原胞中的 8 行零速度经过扩胞后被复制为 72 行，而不是被清除。

## 问题来源

### ASE 数据传播

`ase.io.vasp.read_vasp()` 读取带速度区块的 CONTCAR 后，会在 `Atoms` 中
创建 `momenta` 数组。`ase.io.vasp.write_vasp()` 在发现 `momenta` 时会自动
写出离子速度区块。

在本机 `vdwID` 环境的 ASE 3.28.0 中，读取实际 CONTCAR 得到：

```text
arrays: ['momenta', 'numbers', 'positions']
has_momenta: True
velocity range: 0.0 .. 0.0
```

实际问题最初在 ASE 3.29.0 使用过程中发现；两版都表现出速度传播行为。

### 两条 Stage1 路径都受影响

`build.py` 当前调用：

```python
_write_md_poscar(
    source_dir / "CONTCAR",
    target / "POSCAR",
    config.sc if not config.sc_rlx else None,
)
```

- `sc_rlx: true`：`read_vasp() -> write_vasp()` 直接保留 `momenta`。
- `sc_rlx: false`：`make_supercell() -> sort()` 复制 `momenta`，扩胞后的每个
  周期镜像都得到对应速度，然后 `write_vasp()` 将其写出。

因此该问题与 P1-1 的 Selective Dynamics 行为不同：约束在当前扩胞路径中
可能丢失，但 `momenta` 作为原子数组会被扩胞传播。

### 单层 MD 也存在同类入口

Stage1 的 `top_layer` 和 `bot_layer` MD 目录通过
`write_supercell_poscar()` 读取输入层 POSCAR 并扩胞。如果输入层 POSCAR
意外携带速度，同样可能把速度写入单层 MD POSCAR。虽然本次实际样例来自
弛豫 CONTCAR，解决方案仍应覆盖所有 Stage1 MD 写出路径。

## 原有保护为何失效

原始设计文档明确要求：

> ASE read/write of `CONTCAR` to `POSCAR` without preserving the velocity
> block.

仓库中也有对应测试。测试向单原子 CONTCAR 追加：

```text
Cartesian
9.0 9.0 9.0
```

随后只检查：

```python
assert "9.0 9.0 9.0" not in poscar_text
```

ASE 写出时会把数值格式化为：

```text
9.0000000000000000  9.0000000000000000  9.0000000000000000
```

所以短字符串断言没有命中，测试错误通过，但速度区块实际仍在。使用 ASE
3.28.0 运行现有测试得到 `1 passed`，检查其生成文件则确认速度区块完整
存在。

根因既包括生产代码没有显式清除 `momenta`，也包括测试只检查某一种文本
格式，没有验证 POSCAR 的结构语义。

## 科学影响

VASP 官方说明：如果 POSCAR 没有初速度，MD 会按照 `TEBEG` 对应的
Maxwell-Boltzmann 分布随机初始化速度；如果 POSCAR 已提供速度，则使用
提供的速度。

零速度区块会使新 MD 从 0 K 启动。其影响包括：

- 初始升温过程与预期的目标温度初始化不一致；
- 平衡阶段被额外的升温过程占用；
- 早期轨迹的温度、构型和力分布发生偏差；
- 如果早期帧进入 MLFF 数据集，可能改变训练数据分布；
- 不同计算对初温处理的差异降低数据生成流程的一致性。

参考：

- <https://vasp.at/wiki/TEBEG>
- <https://vasp.at/wiki/index.php/POSCAR>

## 已确认的解决方案

### Stage1 行为

1. 使用 `build stage1` 时，无条件删除弛豫 CONTCAR 中的离子速度信息。
2. 删除必须发生在 MD POSCAR 写出前；扩胞路径宜在扩胞前清除，以避免先
   复制无用的 `momenta`。
3. `sc_rlx: true` 和 `sc_rlx: false` 两条路径必须显式执行相同行为。
4. Stage1 生成的单层 MD POSCAR 也必须清除输入结构可能携带的速度。
5. 不提供保留速度的 Stage1 配置开关，避免“新 MD”与“MD 续算”语义混合。

实现时应使用 ASE 的公开原子 API 清除速度/动量数据，而不是依赖截断文本、
固定行数或 `write_vasp()` 的隐含版本行为。

### MD restart 行为

从旧 MD 的 CONTCAR 延续轨迹时，保留速度是正确行为，但这不属于当前
Stage1。未来如有需求，应单独设计明确的 restart 工作流，并显式区分：

- 新 MD：删除输入速度，由 VASP 按 `TEBEG` 初始化；
- 续算 MD：保留旧 MD 的位置、速度以及其他必要的连续状态。

restart 功能目前没有进一步开发计划，已加入自动化待开发专题。

## 后续实现的验收条件

以下仅记录未来实现要求，本 note 不包含代码修改：

- `sc_rlx: true`：源 CONTCAR 有全零或非零速度时，MD POSCAR 都没有速度
  区块。
- `sc_rlx: false`：扩胞前后的原子数无论如何变化，MD POSCAR 都没有速度
  区块。
- 单层输入 POSCAR 有速度时，生成的单层 MD POSCAR 没有速度区块。
- 重新使用 `read_vasp()` 读取生成的 MD POSCAR 后，`Atoms` 不含
  `momenta`。
- 测试必须验证结构语义，不能只搜索某个浮点数的特定文本格式。
- 测试至少覆盖全零速度、非零速度、直接重写、扩胞和单层 MD。
- MD INCAR 中的 `TEBEG` 可在无输入速度的条件下正常决定初始速度分布。
