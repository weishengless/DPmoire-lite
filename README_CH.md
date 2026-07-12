# DPmoire-lite 中文说明

英文说明见 [README.md](README.md)。详细工作流说明见 [workflow_CH.md](workflow_CH.md)，英文版见 [workflow.md](workflow.md)。

DPmoire-lite 是从原始 DPmoire 工作流中整理出来的干净 VASP 数据集生成框架。它用于生成双层或莫尔体系力场数据集所需的 VASP 计算目录，可选择提交 Slurm 作业，并从完成的计算中收集独立的 `extxyz` 数据集。

这个项目只负责数据准备：不训练模型，也不会自动合并弛豫、MD 和验证集数据。

## 快速开始

```bash
DPmoireLite init-example my_case
cd my_case
# 先修改 config.yaml、input 文件、scripts 文件和 potcar_dir。
DPmoireLite build config.yaml
DPmoireLite collect config.yaml --stage rlx
DPmoireLite collect config.yaml --stage md
DPmoireLite collect config.yaml --stage validation
```

`init-example` 会复制一个自包含模板，其中包含 `config.yaml`、`input/` 和 `scripts/`。生成出的 `config.yaml` 已经为每个支持的配置项写了行内注释。

## 在自己的 Conda 环境中安装

克隆私有仓库，激活自己的 conda 环境，然后在仓库根目录中安装这个 Python 包：

```bash
git clone https://github.com/weishengless/DPmoire-lite.git
cd DPmoire-lite
conda activate your_env_name
python -m pip install -e .
DPmoireLite --help
```

如果不需要可编辑安装，可以把 `python -m pip install -e .` 换成：

```bash
python -m pip install .
```

## 必需输入文件

`input_dir` 中必须包含以下结构文件和 INCAR 模板：

- `top_layer.poscar`：上层单层结构，可以是 primitive cell，也可以是已经匹配好的单层晶胞。
- `bot_layer.poscar`：下层单层结构，可以是 primitive cell，也可以是已经匹配好的单层晶胞。
- `init_INCAR`：初始单层 MLFF 计算使用的 INCAR 模板。
- `rlx_INCAR`：堆垛弛豫计算使用的 INCAR 模板。
- `MD_INCAR`：双层 MD 计算使用的 INCAR 模板。
- `MD_monolayer_INCAR`：可选单层 MD 计算使用的 INCAR 模板。
- `val_INCAR`：可选 twist validation 计算使用的 INCAR 模板。

层结构 POSCAR 必须使用 slab cell：面内晶格矢量位于 Cartesian xy 平面，c 矢量沿 Cartesian z 方向。由于层间距模式沿 z 方向工作，DPmoire-lite 会拒绝倾斜的 slab cell。

如果某个 INCAR 模板中包含 `LUSE_VDW = T`，则 `input_dir` 中还必须有 `vdw_kernel.bindat`，它会被复制到生成出的计算目录中。

`script_dir` 中必须包含 `dft_script` 指定的 Slurm 提交脚本。

`potcar_dir` 应指向 VASP POTCAR 根目录。默认 `potcar_policy: recommend` 时，DPmoire-lite 会先尝试 VASP 推荐的元素映射目录，例如 Li 对应 `Li_sv`，然后再回退到普通元素名目录。设置 `potcar_policy: minimal` 时，会在 `potcar_dir` 中选择常规 POTCAR 候选里 `ZVAL` 最小的目录。

## 工作流

`stage: 0` 生成第一批计算目录：

- 如果 `init_mlff: true`，生成 `init_mlff/`。
- 如果 `do_relaxation: true`，生成 `rlx/<i>_<j>/` 弛豫目录。
- 如果 `twist_val: true`，生成 `validation/<angle>/` 验证集目录。

`stage: 1` 在 `md/` 下生成 MD 目录。它会在写入任何 MD 目录前检查弛豫输出是否完整并收敛。当 `vasp_ml: true` 时，它还会要求 `init_mlff/ML_ABN` 和 `init_mlff/ML_FFN` 存在，并把它们作为 `ML_AB` 和 `ML_FF` 分发到每个 MD 目录中。

`stage: all` 当前暂时不可用，因为 Slurm 终态校验和失败传播尚不可靠。请分别使用 `submit: false` 生成 Stage0 和 Stage1，手动提交，并在生成下一阶段前检查上一阶段的输出。

validation 输出独立于 MD 时间线：stage1 不会使用 validation 结果，validation 数据也只会在用户显式运行以下命令时收集：

```bash
DPmoireLite collect config.yaml --stage validation
```

## 提交语义

`submit: false` 只生成计算目录，也是当前推荐的工作流。请手动提交生成的目录、检查输出，然后再生成下一阶段。

`submit: true` 但不加 `--wait` 时，会生成目录、提交当前 stage 请求的作业，然后退出。这个 fire-and-forget 模式只保证 `sbatch` 调用成功，不保证作业最终成功，也不会自动推进依赖阶段。进程不会持续轮询 Slurm，因此无法执行 `n_nodes` 节流或 `auto_resub`。

Stage0 和 Stage1 的 `submit: true` 与 `--wait` 组合当前暂时关闭，`stage: all` 在所有模式下都不可用。因此，在 submitted wait 关闭期间，`auto_resub` 不应被视为可用于生产。

初始 MLFF 流程是显式设计的：

- 手动模式：stage0 会创建第一步 `init_mlff` 作业；如果 `submit: true` 但不加 `--wait`，只会提交这第一步 init 作业。stage0 仍会继续生成并可选提交已经启用的弛豫或 validation 目录。用户完成 `ML_ABN` 和 `ML_FFN` 准备后，stage1 再使用这些文件。
- 当前必须在检查前一阶段输出后，手动完成第二步 init MLFF 和 Stage0 到 Stage1 的切换。

## 数据收集语义

收集逻辑是宽容的。缺失或无法读取的来源会尽量跳过，并在对应 stage 的 manifest 中记录收集帧数、跳过项和失败项。

输出文件保持分开：

- `work_dir/rlx_data.extxyz` 来自 `collect --stage rlx`
- `work_dir/MD_data.extxyz` 来自 `collect --stage md`
- `work_dir/valid.extxyz` 来自 `collect --stage validation`

对于弛豫数据，DPmoire-lite 会读取配置匹配到的 OUTCAR 系列，并按照 `outcar_collect_freq` 每隔若干 ionic step 取一帧。

对于 MD 数据：

- 如果 `vasp_ml: true`，DPmoire-lite 读取 `ML_ABN`，只收集 ab initio 构型。如果 `ML_AB` 已经存在，则会跳过已经出现过的构型。
- 如果 `vasp_ml: false`，DPmoire-lite 读取 OUTCAR，并使用 `outcar_collect_freq` 控制采样间隔。

对于 validation 数据，会以频率 1 收集所有 OUTCAR ionic step。

## 配置项说明

配置文件只接受 snake_case 键名。旧 DPmoire 名称如 `VASP_ML`、`K-mesh`、`POTCAR_dir`、`DFT_script`、`ENMAX` 和 `OUTCAR_collect_freq` 会被拒绝。

| 配置项 | 类型 | 含义 |
| --- | --- | --- |
| `dft_script` | 字符串 | Slurm 提交脚本文件名。该文件会从 `script_dir` 复制到每个生成的计算目录中，并用 `sbatch` 提交。 |
| `potcar_dir` | 路径 | POTCAR 子目录的根目录。 |
| `potcar_policy` | `recommend` 或 `minimal` | POTCAR 选择策略。`recommend` 使用 VASP 推荐映射并作为默认值；`minimal` 会扫描 `potcar_dir` 中的常规 POTCAR 变体并选择 `ZVAL` 最小的候选。 |
| `script_dir` | 路径 | 存放提交脚本的目录。 |
| `input_dir` | 路径 | 存放单层 POSCAR、INCAR 模板和可选 `vdw_kernel.bindat` 的目录。 |
| `work_dir` | 路径 | 生成 stage、manifest、备份目录和数据集文件的根目录。 |
| `n_nodes` | 正整数 | 为 DPmoire-lite 等待模式节流预留。submitted `--wait` 当前关闭；非等待模式提交请求的作业后退出，不执行节流。 |
| `stage` | `0`、`1` 或 `all` | 构建阶段。`0` 生成 init、rlx 和 validation 目录；`1` 从完成的弛豫输出生成 MD 目录；`all` 当前暂时不可用。 |
| `submit` | 布尔值 | `false` 只生成目录；`true` 会用 Slurm 提交生成的目录。 |
| `auto_resub` | 布尔值 | 为 submitted wait 工作流预留；在该工作流关闭期间不具备生产可用性。非等待模式忽略。 |
| `vasp_ml` | 布尔值 | 是否使用 VASP MLFF 工作流。stage1 会分发 `init_mlff/ML_ABN` 和 `init_mlff/ML_FFN`；MD 收集会读取 `ML_ABN` 而不是 OUTCAR。 |
| `outcar_collect_freq` | 正整数 | 弛豫和非 ML MD 的 OUTCAR 采样间隔。validation 始终使用 1。VASP-ML MD 收集读取 `ML_ABN`，不受此项影响。 |
| `do_relaxation` | 布尔值 | stage0 是否生成 `rlx/` 下的弛豫目录。 |
| `init_mlff` | 布尔值 | stage0 是否生成初始 `init_mlff/` 目录。 |
| `sc_rlx` | 布尔值 | `true` 表示弛豫超胞堆垛结构；`false` 表示只弛豫 primitive glide structure，并在 stage1 根据 CONTCAR 扩胞。 |
| `preserve_grid_shift_md` | 布尔值 | Stage1 默认清除全部约束。设为 `true` 时只保留 DPmoire-lite 的网格平移锚点；`F F T` 表示固定 x/y、允许 z 移动。 |
| `n_sectors` | 整数或 `[nx, ny]` | 堆垛平移采样网格。`9` 等价于 `[9, 9]`，`[9, 8]` 表示矩形网格。 |
| `sc` | 整数或 `[sx, sy]` | MD 使用的超胞扩展；当 `sc_rlx: true` 时也用于弛豫。`2` 等价于 `[2, 2]`。 |
| `d` | 数值 | 距离数值，具体物理含义由 `d_mode` 决定。 |
| `d_mode` | `surface_gap` 或 `reference_plane_gap` | `surface_gap` 表示 `min_z(top) - max_z(bot) = d`；`reference_plane_gap` 表示所选参考原子的平均 z 坐标差为 `d`。默认值为 `surface_gap`。 |
| `d_reference` | 映射，可选 | `reference_plane_gap` 使用的参考原子选择器，例如内置 MoTe2 示例可用 `{top: [Mo], bot: [Mo]}`。省略或使用 `all` 表示该层所有原子。 |
| `k_mesh` | 整数 | KPOINTS 目标值。程序会根据生成后 POSCAR 的面内晶格长度写 Gamma-centered mesh。 |
| `encut_factor` | 数值 | INCAR 中的 `ENCUT` 会写成 `encut_factor * max(POTCAR ENMAX)`。 |
| `r_cut` | 数值 | 写入 `ML_RCUT1` 和 `ML_RCUT2` 的值。若为负数，则根据最大输入单层面内晶格长度和 `d` 自动估算。 |
| `symm_reduce` | 布尔值 | 是否用 `pymatgen`/`spglib` 对堆垛平移做对称性约化，并写出 `sym_reduced_stackings.txt`。 |
| `twist_val` | 布尔值 | stage0 是否生成 twist validation 计算目录。validation 不属于 MD 时间线。 |
| `min_val_n` | 整数 | twist validation 搜索使用的最小 `n`。只在 `twist_val: true` 时使用。 |
| `max_val_n` | 整数 | twist validation 搜索使用的最大 `n`。只在 `twist_val: true` 时使用。 |
| `include_monolayer_md` | 布尔值 | stage1 是否额外生成 `md/top_layer` 和 `md/bot_layer` 单层 MD 目录。 |
| `outcar_patterns` | 正则字符串列表，可选 | 收集时用于识别 OUTCAR 系列的正则列表。默认匹配 `OUTCAR`、`OUTCAR<number>`、`OUT<number>` 和 `out<number>`。 |

## 目录替换策略

当即将生成的子目录已经存在时，DPmoire-lite 会把该子目录备份为带时间戳后缀的目录，然后重新生成。备份范围只限当前要重新生成的 stage 子目录，不会整体移动 `work_dir`。

## 命令行

```bash
DPmoireLite init-example my_case
DPmoireLite build config.yaml
DPmoireLite collect config.yaml --stage rlx
DPmoireLite collect config.yaml --stage md
DPmoireLite collect config.yaml --stage validation
```
