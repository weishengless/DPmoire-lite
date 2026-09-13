# P1-1：MD POSCAR 中的 Selective Dynamics 传播

- 状态：讨论已完成
- 评审日期：2026-07-11
- 结论日期：2026-07-11
- 优先级：P1
- 关联位置：
  - `src/dpmoire_lite/structures.py:324-338`
  - `src/dpmoire_lite/build.py:101`
  - `src/dpmoire_lite/build.py:379-387`
  - `example/config.yaml:33`

## 最终问题表述

该问题并不影响所有 MD POSCAR 生成路径，其是否出现取决于
`sc_rlx` 和弛豫后 `CONTCAR` 中是否仍含 Selective Dynamics：

- `sc_rlx: true`：Stage1 直接执行 `read_vasp() -> write_vasp()`。
  如果源 `CONTCAR` 仍含 Selective Dynamics，ASE 会将其重新写入 MD
  POSCAR，项目生成的两个面内固定约束表现为 `F F T`。
- `sc_rlx: false`：Stage1 当前经过 `make_supercell() -> sort()`。
  在 ASE 3.29.0 中，新结构没有继承源结构的约束，因此生成的 MD
  POSCAR 不含 Selective Dynamics。
- 如果源 `CONTCAR` 本身不含 Selective Dynamics，则无论后续是否直接
  重写，都不会凭空产生约束。

因此，原评审将问题泛化到所有 MD 生成路径并不准确。P1 的准确范围是：

> 当 `sc_rlx: true`，并且弛豫后的 `CONTCAR` 仍包含 Selective
> Dynamics 时，约束会进入 MD POSCAR；`sc_rlx: false` 的当前扩胞路径
> 不会保留约束。

## 最小复现证据

使用 ASE 3.29.0 得到：

| 配置 | MD POSCAR 结果 |
| --- | --- |
| `sc_rlx: true` | 保留 `Selective dynamics` 和两个 `F F T` |
| `sc_rlx: false` | 扩胞时约束丢失，不含 `Selective dynamics` |

可对实际计算目录进行以下检查：

```powershell
Select-String -Path work\rlx\0_0\CONTCAR -Pattern "Selective"
Select-String -Path work\md\0_0\POSCAR -Pattern "Selective"
```

项目示例配置默认使用 `sc_rlx: false`，所以示例配置下观察不到约束进入
MD POSCAR，与当前代码行为一致。

## 问题来源

### 上游来源

Stage0 构造弛豫结构时，`StructureHandler.shift_atoms()` 或
`shift_primitive_atoms()` 为上下层各选择一个原子并设置 `FixedLine`，方向
为晶胞 c 轴。ASE 写入 POSCAR 后，对应两个原子的选择性动力学标记为
`F F T`，其余原子为 `T T T`。

该约束用于限制层间堆垛弛豫过程中的整体面内滑移，而不是普遍冻结超胞内
每个原胞的对应原子。

### 直接原因

`build.py` 根据 `sc_rlx` 选择不同的 MD POSCAR 写出路径：

```python
_write_md_poscar(
    source_dir / "CONTCAR",
    target / "POSCAR",
    config.sc if not config.sc_rlx else None,
)
```

当第三个参数为 `None` 时，代码直接读写源结构，ASE 会保留其约束。当需要
扩胞时，ASE 3.29.0 当前生成的新结构不携带原约束。

### 设计缺口

约束是否进入 MD 目前由 `sc_rlx` 分支和 ASE 的约束传播行为间接决定，配置
中没有表达用户是否希望在 MD 中保留层间滑移锚点。尤其不能把当前
`make_supercell()` 丢弃约束的行为当作稳定、显式的清除保证。

## 科学影响

如果两个 `F F T` 锚点非预期地进入 MD，它们会继续限制对应原子的面内
运动，从而影响轨迹、力以及后续训练数据。项目的主要目标是生成不同
glide/stacking 构型的训练数据，因此默认 MD 行为应当是不保留这些弛豫
锚点。

另一方面，受约束 MD 仍可能是用户有意选择的实验方式，因此不应完全移除
这种能力，而应提供显式配置。

## 已确认的解决方案

新增配置：

```yaml
preserve_grid_shift_md: false
```

配置语义：

1. 默认值为 `false`，符合项目生成不同 glide/stacking 训练数据的目标。
2. 为 `false` 时，所有 Stage1 MD 输出都显式清除全部 ASE/Selective
   Dynamics 约束，包括两条 bilayer `sc_rlx` 路径和单层 MD。不能依赖
   `make_supercell()` 当前是否传播约束。
3. `true` 只表示保留 DPmoire-lite 自己生成的 bilayer grid-shift anchors，
   不表示传播任意用户约束。
4. 为 `true` 且源 `CONTCAR` 的约束与 manifest 中 Stage0 锚点完全一致时：
   - 不扩胞的路径保留上下层各一个锚点；
   - 扩胞路径仍只固定上下层各一个确定性的代表原子，总数保持为两个。
5. 扩胞时不得把原胞的两个锚点复制到每个周期镜像。否则 `nx * ny` 扩胞
   会产生每层 `nx * ny` 个受约束原子，共 `2 * nx * ny` 个，过度抑制
   超胞的局部面内运动，并与 `sc_rlx: true` 路径的物理语义不一致。
6. 为 `true` 时，源 CONTCAR 缺少预期锚点、锚点索引/掩码改变、出现额外
   受约束原子或出现其他约束类型，都在修改 MD 目录前明确报错。程序既不
   丢弃额外约束后继续，也不凭空重建缺失锚点。
7. 单层 MD 永远清除全部约束。`preserve_grid_shift_md` 不适用于单层，因为
   单层没有 Stage0 bilayer grid-shift anchor。输入单层 POSCAR 含约束时清除
   并警告。
8. 需要传播任意用户约束时，应未来设计独立、明确的通用约束策略，不复用
   `preserve_grid_shift_md`。

## Stage0 锚点 provenance

新 relaxation manifest 必须为每个 stacking 记录 Stage0 最终写出 POSCAR
中的项目锚点：

```yaml
grid_shift_anchors:
  rlx/0_0:
    atom_count: 8
    top_index: 0
    bottom_index: 4
    fixed_masks:
      0: [true, true, false]
      4: [true, true, false]
```

索引以实际写出、排序后的 Stage0 POSCAR 为准，并与 P1-5 的 POSCAR hash、
元素组成和原子数绑定。Stage1 校验 VASP CONTCAR 仍保持相同原子顺序和精确
约束集合。

`fixed_mask`/`fixed_masks` 使用 ASE 约束语义：`true` 表示该方向固定，
`false` 表示允许移动。因此 POSCAR 的 `F F T` 对应
`[true, true, false]`，与 ASE 3.28 `FixScaled.mask` 一致。字段不得使用含糊的
`mask` 名称或把 POSCAR 的 T/F 文本语义直接当成布尔值。

旧 manifest 没有 anchor provenance 时：

- `preserve_grid_shift_md: false` 可以继续，因为行为是无条件清除全部约束；
- `preserve_grid_shift_md: true` 不能安全区分项目锚点和用户约束，必须报错并
  要求使用默认清除策略，或先完成明确的 legacy provenance migration。

## 扩胞锚点映射

`sc_rlx: false` 扩胞时，以 manifest 记录的两个 primitive anchor 为来源：

1. 验证 CONTCAR 中只有这两个 `F F T`；
2. 扩胞前为每个原子附加稳定 `source_index`；
3. 扩胞时同时为每个镜像生成整数 `image_translation`/`image_id`，对当前对角
   超胞可表示为 `[tx, ty, tz]`，并满足 `0 <= tx < sx`、
   `0 <= ty < sy`、`tz = 0`；
4. 每个扩胞原子的稳定身份是 `(source_index, image_translation)`，不能只有
   source index，因为同一个 source atom 会产生多个周期镜像；
5. 选择 `image_translation == [0, 0, 0]` 的两个 source anchors；
6. `sort()` 后通过完整稳定身份恢复最终索引；
7. 删除临时 identity arrays，并只为这两个最终原子设置 `FixedLine`；
8. MD manifest 记录最终 anchor index、source index 和 image translation，
   便于复核。

不能依赖排序后的 `top_idx[0]` 偶然对应原锚点，也不能复制每个周期镜像的
约束。

## 为什么扩胞后仍只固定两个原子

当前 `sc_rlx: true` 在直接生成超胞弛豫结构时，无论超胞大小如何，都只
选择 `top_idx[0]` 和 `bot_idx[0]`，即上下层各一个锚点。扩胞后也保持两个
锚点，可以让两条路径具有一致的约束密度和物理含义。

严格复制原胞约束虽然符合周期镜像映射，但会随超胞面积增加受约束原子
数量，更强地抑制面内振动和局部形变，不符合该约束仅用于阻止整体平面
漂移的目的。

## 后续实现的验收条件

以下内容仅记录未来实现应满足的结果，本 note 不包含代码修改：

- 配置缺省或显式为 `false` 时，bilayer 和 monolayer MD POSCAR 均完全不含
  Selective Dynamics 约束。
- 配置为 `true` 且源 CONTCAR 含项目滑移锚点时，两条路径生成的 MD
  POSCAR 均只有上下层各一个 `F F T`。
- 扩胞倍率变化不会改变受约束原子的总数；总数始终为两个。
- `true` 时缺少锚点、存在额外用户约束或约束掩码变化均在 MD 目录修改前
  报错。
- 新 manifest 记录每个 stacking 的两个 anchor index 和 mask。
- fixed mask 明确使用 `true = fixed`，`F F T` 为 `[true, true, false]`。
- 扩胞身份同时包含 source index 和 translation/image ID；同一 source 的不同
  镜像不会混淆。
- 旧 manifest 只有在 `preserve_grid_shift_md: false` 时兼容继续。
- 单层输入含约束时始终清除，并产生一次警告。
- 示例配置和打包后的示例配置都明确展示默认值 `false`。
- 回归测试不得依赖 ASE 当前在 `make_supercell()` 中偶然丢弃约束的行为。

---

## 追记（2026-09-13）：锚点原子可由用户按元素指定

- 新增配置 `grid_shift_anchor`：逐层元素选择器（`top`/`bot` 映射，或单个元素
  同时用于两层），在每层选择该元素的第一个原子作为滑移锚点。键缺省时保持
  本 note 原有行为（排序后每层第一个原子）不变。
- 选择器在 preflight 构建组合结构时立即校验：元素在该层不存在则报错并列出
  该层可用元素，发生在任何 stage 目录创建之前。
- 本 note 的锚点物理契约不变：仍为每层恰好一个锚点、fixed mask
  `[true, true, false]`、扩胞后受约束原子总数保持两个；manifest 的
  `grid_shift_anchors` 记录与 Stage1 校验逻辑不受影响。
- 设计决定（用户批准）：每层 1 个锚点、逐层元素选择器、缺省 auto、层内取
  首个匹配原子。动机：异质结两层元素不等价时（如 NbSe2/TaSe2），排序巧合
  会在下层固定金属、上层固定硫属。
- 同日追记：新增 `selection: nearest_pair`（映射内）与裸关键字
  `grid_shift_anchor: nearest_pair` —— 在两层候选集内按 PBC 最近配对选取
  锚点，并列取索引序最小的一对。`first` 语义与缺省行为不变；物理契约
  （每层 1 锚点、`[true, true, false]`、扩胞总数 2）不变。
