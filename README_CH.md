# DPmoire-lite 中文说明

英文说明见 [README.md](README.md)。

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

如果某个 INCAR 模板中包含 `LUSE_VDW = T`，则 `input_dir` 中还必须有 `vdw_kernel.bindat`，它会被复制到生成出的计算目录中。

`script_dir` 中必须包含 `dft_script` 指定的 Slurm 提交脚本。

`potcar_dir` 应指向 VASP POTCAR 根目录。对于新版 VASP 推荐的元素映射目录，DPmoire-lite 会先尝试映射后的目录名，例如 Li 对应 `Li_sv`，然后再回退到普通元素名目录。

## 工作流

`stage: 0` 生成第一批计算目录：

- 如果 `init_mlff: true`，生成 `init_mlff/`。
- 如果 `do_relaxation: true`，生成 `rlx/<i>_<j>/` 弛豫目录。
- 如果 `twist_val: true`，生成 `validation/<angle>/` 验证集目录。

`stage: 1` 在 `md/` 下生成 MD 目录。它会在写入任何 MD 目录前检查弛豫输出是否完整并收敛。当 `vasp_ml: true` 时，它还会要求 `init_mlff/ML_ABN` 和 `init_mlff/ML_FFN` 存在，并把它们作为 `ML_AB` 和 `ML_FF` 分发到每个 MD 目录中。

`stage: all` 是自动提交并等待的工作流。它要求同时设置 `submit: true` 并使用 `DPmoireLite build config.yaml --wait`，因为 stage1 依赖已经完成的 stage0 输出。

validation 是独立时间线。validation 作业不会阻塞 stage1，validation 数据也只会在用户显式运行以下命令时收集：

```bash
DPmoireLite collect config.yaml --stage validation
```

## 提交语义

`submit: false` 只生成计算目录。

`submit: true` 但不加 `--wait` 时，会生成目录、提交当前 stage 请求的所有作业，然后退出。在这个模式下，进程不会持续轮询 Slurm，因此无法执行 `n_nodes` 节流和 `auto_resub` 重提逻辑。

`submit: true` 并加上 `--wait` 时，DPmoire-lite 会在轮询循环中最多保持 `n_nodes` 个活跃 Slurm 作业。如果 `auto_resub: true`，失败作业会按计算目录最多重提一次。

初始 MLFF 流程是显式设计的：

- 手动模式：stage0 只创建并可选提交第一步 `init_mlff` 作业。用户完成 `ML_ABN` 和 `ML_FFN` 准备后，stage1 再使用这些文件。
- 自动模式：`stage: all`、`submit: true` 和 `--wait` 会在生成 stage1 前跑完两步 init MLFF 依赖链。

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
| `potcar_dir` | 路径 | POTCAR 子目录的根目录。DPmoire-lite 会优先使用 VASP 推荐的元素目录映射，然后回退到普通元素名。 |
| `script_dir` | 路径 | 存放提交脚本的目录。 |
| `input_dir` | 路径 | 存放单层 POSCAR、INCAR 模板和可选 `vdw_kernel.bindat` 的目录。 |
| `work_dir` | 路径 | 生成 stage、manifest、备份目录和数据集文件的根目录。 |
| `n_nodes` | 正整数 | `--wait` 模式下最多保持的活跃 Slurm 作业数。非等待模式会提交所有作业后退出。 |
| `stage` | `0`、`1` 或 `all` | 构建阶段。`0` 生成 init、rlx 和 validation 目录；`1` 从完成的弛豫输出生成 MD 目录；`all` 运行自动依赖链。 |
| `submit` | 布尔值 | `false` 只生成目录；`true` 会用 Slurm 提交生成的目录。 |
| `auto_resub` | 布尔值 | 在 `--wait` 模式下，对失败 Slurm 作业按计算目录最多重提一次。非等待模式忽略。 |
| `vasp_ml` | 布尔值 | 是否使用 VASP MLFF 工作流。stage1 会分发 `init_mlff/ML_ABN` 和 `init_mlff/ML_FFN`；MD 收集会读取 `ML_ABN` 而不是 OUTCAR。 |
| `outcar_collect_freq` | 正整数 | 弛豫和非 ML MD 的 OUTCAR 采样间隔。validation 始终使用 1。VASP-ML MD 收集读取 `ML_ABN`，不受此项影响。 |
| `do_relaxation` | 布尔值 | stage0 是否生成 `rlx/` 下的弛豫目录。 |
| `init_mlff` | 布尔值 | stage0 是否生成初始 `init_mlff/` 目录。 |
| `sc_rlx` | 布尔值 | `true` 表示弛豫超胞堆垛结构；`false` 表示只弛豫 primitive glide structure，并在 stage1 根据 CONTCAR 扩胞。 |
| `n_sectors` | 整数或 `[nx, ny]` | 堆垛平移采样网格。`9` 等价于 `[9, 9]`，`[9, 8]` 表示矩形网格。 |
| `sc` | 整数或 `[sx, sy]` | MD 使用的超胞扩展；当 `sc_rlx: true` 时也用于弛豫。`2` 等价于 `[2, 2]`。 |
| `d` | 数值 | 构造双层和 validation 结构时使用的层间距。 |
| `k_mesh` | 整数 | KPOINTS 目标值。程序会根据面内晶格长度和当前超胞尺度写 Gamma-centered mesh。 |
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
DPmoireLite build config.yaml --wait
DPmoireLite collect config.yaml --stage rlx
DPmoireLite collect config.yaml --stage md
DPmoireLite collect config.yaml --stage validation
```
