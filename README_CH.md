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
- `init_INCAR`：向后兼容的手动 init MLFF 路径使用的 INCAR 模板。
- `init_bottom_INCAR` 和 `init_top_INCAR`：`init_mlff_mode: single-job`
  使用的两份独立科学模板。`MAGMOM`、DFT+U 等依赖元素的设置必须由用户在
  对应模板中明确给出；DPmoire-lite 不会猜测或用脚本修补这些标签。
- `rlx_INCAR`：堆垛弛豫计算使用的 INCAR 模板。
- `MD_INCAR`：双层 MD 计算使用的 INCAR 模板。
- `MD_monolayer_INCAR`：可选单层 MD 计算使用的 INCAR 模板。
- `val_INCAR`：可选 twist validation 计算使用的 INCAR 模板。

层结构 POSCAR 必须使用 slab cell：面内晶格矢量位于 Cartesian xy 平面，c 矢量沿 Cartesian z 方向。由于层间距模式沿 z 方向工作，DPmoire-lite 会拒绝倾斜的 slab cell。

如果某个 INCAR 模板中包含 `LUSE_VDW = T`，则 `input_dir` 中还必须有 `vdw_kernel.bindat`，它会被复制到生成出的计算目录中。

`script_dir` 中必须包含 `dft_script` 指定的 Slurm 提交脚本。使用
`init_mlff_mode: single-job` 时，该文件是显式 Bash 模板：Slurm 指令必须位于
可执行内容之前，同步启动函数必须精确声明为 `dpmoire_run_vasp() {`，启动位置必须是：

```bash
dpmoire_run_vasp # DPMOIRE-LITE:RUN
```

该函数必须等待 VASP 结束并返回真实 launcher exit code，不能放到后台运行。函数结束的
`}` 必须单独成行；标记调用必须是最后一条可执行语句，后面只能有注释或空行。
DPmoire-lite 只替换派生副本中的这一个标记行，不会搜索或改写函数体里的 `srun`、
`mpirun`、container、pipeline 或重定向。

自动提交 `single-job` 时，模板不能通过 `#SBATCH --wait`、`-W` 或含 `W` 的合法
短选项组合请求 Slurm wait；弃用的 `#SLURM -W` 写法以及每个 `hetjob`/`packjob`
component 都会检查。由于 `sbatch` 可能翻译其他调度器语法，自动模式还会拒绝所有
模板任意位置以 `#PBS` 或 `#BSUB` 开头的内容；请改用原生 `#SBATCH`，或设置
`submit: false`。preflight 会在写入工作目录之前拒绝。这些限制不适用于
`submit: false`，因为后续手动执行 `sbatch` 时由用户自行决定是否等待。

`potcar_dir` 应指向 VASP POTCAR 根目录。默认 `potcar_policy: recommend` 时，DPmoire-lite 会先尝试 VASP 推荐的元素映射目录，例如 Li 对应 `Li_sv`，然后再回退到普通元素名目录。设置 `potcar_policy: minimal` 时，会在 `potcar_dir` 中选择常规 POTCAR 候选里 `ZVAL` 最小的目录。

## 工作流

`stage: 0` 生成第一批计算目录：

- 如果 `init_mlff: true`，按 `init_mlff_mode` 选择的布局生成
  `init_mlff/`。
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

- `manual` 是默认模式。stage0 会创建历史兼容的单目录 `init_mlff`
  作业；如果 `submit: true` 但不加 `--wait`，只提交这第一步 init 作业。
  stage0 仍会继续生成并可选提交已经启用的弛豫或 validation 目录。用户完成
  `ML_ABN` 和 `ML_FFN` 准备后，stage1 再使用这些文件。
- `single-job` 是显式 opt-in。stage0 会事务性发布
  `init_mlff/bottom/` 与 `init_mlff/top/` 两套完整静态输入，分别使用
  `init_bottom_incar` 和 `init_top_incar`。每个 phase 都有自己的 POSCAR、
  局部元素 POTCAR、按自身晶胞生成的 KPOINTS、渲染后的 INCAR 和提交脚本副本；
  两份 INCAR 共用全工作流 cutoff。同时会从带标记的源模板渲染
  `init_mlff/<dft_script>`，且源模板字节不变。manifest 会记录源脚本和派生脚本
  hash 以及有界 workflow 证据，不记录 POTCAR 内容。
- 使用 `submit: false` 时，build 在生成后结束；检查派生脚本，然后进入
  `<work_dir>/init_mlff` 并执行一次 `sbatch <dft_script>`。使用 `submit: true`
  且不加 `--wait` 时，DPmoire-lite 只对这个根脚本发起一次 `sbatch`，把返回的
  job ID 和派生脚本 hash 写入 `init_mlff/manifest.yaml`，随后退出，但不会宣称
  workflow 已完成。派生 adapter 根据 Slurm 的提交目录定位 workflow，因此按此
  目录提交可保证工作区整体移动后仍然可用。同一个 allocation 会依次运行 bottom、
  校验并复制 continuation seed、运行 top、再次校验，并原子发布最终的
  `init_mlff/ML_ABN` 与 `ML_FFN`。`DPMOIRE_PHASE` 在两步中分别为 `step1` 和
  `step2`，可用于区分日志。资源必须同时适用于两个计算，walltime 必须覆盖两步
  总时长。scheduler polling、retry、resume 和跨作业依赖仍未启用。
  DPmoire-lite 会从所有程序化 `sbatch` 的环境中移除继承的 `SBATCH_WAIT`，避免
  fire-and-forget 路径被父 shell 静默改成等待提交。

build CLI 在没有执行任何 `sbatch` 时输出 `build status=generated`，包括只生成或
当前 stage 没有启用目标的情况；只有 fire-and-forget `sbatch` 成功后才输出
`build status=submission_requested`，且它只表示 Slurm 接受了请求。提交失败会返回
非零退出码，并在 init manifest 留下有界 `SUBMIT_FAILED` 证据。
生成的作业只有在两个 phase 和最终 seed 发布都成功后才输出
`init_mlff status=complete`，workflow 失败则输出 `init_mlff status=failed`。
这两个状态之间不轮询调度器。

## 数据收集语义

收集命令返回结构化的汇总状态。CLI 只向 stderr 输出一行简短摘要；所选结果 manifest 保留详细的逐来源诊断。

| 退出码 | 状态 | 含义 |
| ---: | --- | --- |
| `0` | `complete` | 至少发布一帧，且所有预期来源均完整完成。 |
| `1` | `fatal` | 配置、provenance、manifest、恢复或发布失败。 |
| `2` | `degraded` | 已发布帧，但来源覆盖存在 partial、skipped、failed 或未知。 |
| `3` | `no_data` | 本次没有接受或发布新帧。 |

所有非零退出码都是 shell 失败。`no_data` 不会创建、删除或替换 extxyz；如果已有输出，其字节保持不变。

输出文件保持分开：

- `work_dir/rlx_data.extxyz` 来自 `collect --stage rlx`
- `work_dir/MD_data.extxyz` 来自 `collect --stage md`
- `work_dir/valid.extxyz` 来自 `collect --stage validation`

对于弛豫数据，DPmoire-lite 会读取配置匹配到的 OUTCAR 系列，并按照 `outcar_collect_freq` 每隔若干 ionic step 取一帧。

对于 MD 数据：

- `seed-aware` 是默认模式。对于 `vasp_ml: true`，它会验证 Stage1 中不可变的 seed provenance 并排除该前缀；绝不会从当前 `md/ML_AB` 推断前缀。
- 精确 seed 匹配继续使用不变的 `mlab-seed-v1` identity。如果 VASP 只对可信 seed 前缀中的数值进行了重写，fallback 会要求结构和顺序完全一致，并逐分量使用固定、带版本的规则 `abs(a-b) <= 1e-12 * max(1, abs(a), abs(b))`。Manifest v2 reference 必须通过原始文件哈希验证；legacy 情况只能从完整的 `init_mlff/ML_ABN` 明确重建。通过批准的 VASP equivalence 本身仍为 `complete`；只有来源覆盖出现 missing、partial 或 failed 才会 `degraded`。
- 显式 `--mlff-collect-mode full-dedup` 只适用于 MLFF MD。它读取每个可接受的最终 `ML_ABN`，对重复构型保留第一份精确副本（包括一份共享 seed），因此比 `seed-aware` 产生更多 I/O。
- 对弛豫、validation 或非 ML MD 使用 `full-dedup` 会 fatal 并返回 1。
- 如果 `vasp_ml: false`，DPmoire-lite 读取 OUTCAR，并使用 `outcar_collect_freq` 控制采样间隔。

对于 validation 数据，会以频率 1 收集所有 OUTCAR ionic step。

有效的当前 Manifest v2 始终具有最高权威。旧版或显式兼容的缺失 manifest MLFF 收集会写入 `MD_data.collect.yaml`，绝不会伪造或重写 build provenance。缺失 manifest 扫描只允许显式 `full-dedup`；即使扫描产生了帧，由于预期来源覆盖未知，状态至多为 `degraded`。

结果 manifest 在 `collect.dedup.seed_verification` 记录 exact、VASP-equivalent 和 mismatch 聚合计数，并在 `collect.sources[].seed_verification` 记录每个来源的有界证据。diagnostic 只包含哈希、计数、reference trust、首个 mismatch 字段和最大 delta，绝不会包含完整 seed configuration。canonical `mlab-seed-v1`、`mlab-config-v1` 输出以及 full-dedup 行为保持不变。

## 配置项说明

配置文件只接受 snake_case 键名。旧 DPmoire 名称如 `VASP_ML`、`K-mesh`、`POTCAR_dir`、`DFT_script`、`ENMAX` 和 `OUTCAR_collect_freq` 会被拒绝。

| 配置项 | 类型 | 含义 |
| --- | --- | --- |
| `dft_script` | 字符串 | Slurm 提交脚本文件名。manual 模式会原样复制；single-job 模式还要求文档规定的 `dpmoire_run_vasp`/marker 契约，并生成同名 init 专用派生脚本。 |
| `potcar_dir` | 路径 | POTCAR 子目录的根目录。 |
| `potcar_policy` | `recommend` 或 `minimal` | POTCAR 选择策略。`recommend` 使用 VASP 推荐映射并作为默认值；`minimal` 会扫描 `potcar_dir` 中的常规 POTCAR 变体并选择唯一的最低 `ZVAL` 候选；最低值并列时会因歧义而在 preflight 失败。 |
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
| `init_mlff_mode` | `manual` 或 `single-job` | init 布局，默认 `manual`。`single-job` 会生成 bottom/top 工作区和一个根派生脚本。`submit: false` 时用户检查后手动提交一次；fire-and-forget `submit: true` 时 DPmoire-lite 只请求这一个根提交并记录 job ID，不等待完成。 |
| `init_bottom_incar` | 相对路径 | `input_dir` 下的 bottom phase INCAR 模板；`single-job` 必填。 |
| `init_top_incar` | 相对路径 | `input_dir` 下的 top phase INCAR 模板；`single-job` 必填。 |
| `sc_rlx` | 布尔值 | `true` 表示弛豫超胞堆垛结构；`false` 表示只弛豫 primitive glide structure，并在 stage1 根据 CONTCAR 扩胞。 |
| `preserve_grid_shift_md` | 布尔值 | Stage1 默认清除全部约束。设为 `true` 时只保留 DPmoire-lite 的网格平移锚点；`F F T` 表示固定 x/y、允许 z 移动。 |
| `n_sectors` | 整数或 `[nx, ny]` | 堆垛平移采样网格。`9` 等价于 `[9, 9]`，`[9, 8]` 表示矩形网格。 |
| `sc` | 整数或 `[sx, sy]` | MD 使用的超胞扩展；当 `sc_rlx: true` 时也用于弛豫。`2` 等价于 `[2, 2]`。 |
| `d` | 数值 | 距离数值，具体物理含义由 `d_mode` 决定。 |
| `d_mode` | `surface_gap` 或 `reference_plane_gap` | `surface_gap` 表示 `min_z(top) - max_z(bot) = d`；`reference_plane_gap` 表示所选参考原子的平均 z 坐标差为 `d`。默认值为 `surface_gap`。 |
| `d_reference` | 映射，可选 | `reference_plane_gap` 使用的参考原子选择器，例如内置 MoTe2 示例可用 `{top: [Mo], bot: [Mo]}`。省略或使用 `all` 表示该层所有原子。 |
| `k_mesh` | 整数 | KPOINTS 目标值。程序会根据生成后 POSCAR 的面内晶格长度写 Gamma-centered mesh。 |
| `encut_factor` | 数值 | 生成前，DPmoire-lite 会解析 top layer 与 bottom layer 元素并集所选中的 POTCAR 变体。init、relaxation、双层/单层 MD 和 validation 的所有 INCAR 都统一使用该工作流集合中的 `encut_factor * max(ENMAX)`；每个目录的 POTCAR 仍只包含其本地 POSCAR 中的元素，并保持本地 POSCAR 元素顺序。 |
| `r_cut` | 数值 | 写入 `ML_RCUT1` 和 `ML_RCUT2` 的值。若为负数，则根据最大输入单层面内晶格长度和 `d` 自动估算。 |
| `symm_reduce` | 布尔值 | 是否用 `pymatgen`/`spglib` 对堆垛平移做对称性约化，并写出 `sym_reduced_stackings.txt`。 |
| `twist_val` | 布尔值 | stage0 是否生成 twist validation 计算目录。validation 不属于 MD 时间线。 |
| `min_val_n` | 整数 | twist validation 搜索使用的最小 `n`。只在 `twist_val: true` 时使用。 |
| `max_val_n` | 整数 | twist validation 搜索使用的最大 `n`。只在 `twist_val: true` 时使用。 |
| `include_monolayer_md` | 布尔值 | stage1 是否额外生成 `md/top_layer` 和 `md/bot_layer` 单层 MD 目录。 |
| `outcar_patterns` | 正则字符串列表，可选 | 收集时用于识别 OUTCAR 系列的正则列表。默认优先级依次为 `OUTCAR<number>`、`OUT<number>`、`out<number>`，最后是无后缀 `OUTCAR`。 |

## 一次性 Stage 策略

如果任一目标 stage 已存在（包括空目录），DPmoire-lite 会在修改任何文件前停止。它不会移动、备份、覆盖或原地重建 stage。请显式删除完整的冲突 stage，然后重新运行 build。

## 命令行

```bash
DPmoireLite init-example my_case
DPmoireLite build config.yaml
DPmoireLite collect config.yaml --stage rlx
DPmoireLite collect config.yaml --stage md
DPmoireLite collect config.yaml --stage md --mlff-collect-mode full-dedup
DPmoireLite collect config.yaml --stage validation
```
