# DPmoire-lite

DPmoire-lite generates VASP calculation folders and collects `extxyz` datasets for moire and bilayer force-field data construction. It is focused on data preparation: it does not train MLFF models, and it does not automatically merge datasets.

DPmoire-lite 用于为莫尔/双层体系力场数据构建生成 VASP 计算目录，并从计算结果收集 `extxyz` 数据集。它只负责数据准备：不训练 MLFF 模型，也不会自动合并数据集。

## Quick Start / 快速开始

```bash
DPmoireLite init-example my_case
cd my_case
# Edit config.yaml, input files, scripts, and POTCAR path first.
DPmoireLite build config.yaml
DPmoireLite collect config.yaml --stage rlx
DPmoireLite collect config.yaml --stage md
DPmoireLite collect config.yaml --stage validation
```

先用 `init-example` 复制示例工程，然后修改 `config.yaml`、`input/`、`scripts/` 和 `potcar_dir`。`build` 负责生成计算目录，`collect` 只收集用户显式指定的阶段。

## Workflow / 工作流

Stage0 creates the first calculation set. Depending on config flags, it can generate `init_mlff`, relaxation folders under `rlx/`, and optional validation folders under `validation/`.

Stage1 creates MD folders under `md/`. It depends on completed relaxation outputs and, when `vasp_ml: true`, the initialized MLFF files.

Stage0 负责第一批计算目录：根据配置生成 `init_mlff`、`rlx/` 下的弛豫目录，以及可选的 `validation/` 目录。Stage1 负责生成 `md/` 下的 MD 目录；它依赖已经完成的弛豫结果，并在 `vasp_ml: true` 时依赖初始化好的 MLFF 文件。

Manual folder generation is also supported: set the desired `stage` in `config.yaml`, run `DPmoireLite build config.yaml`, inspect or submit the generated folders, then run `collect` only after the relevant VASP jobs have finished.

也可以手动分阶段生成目录：在 `config.yaml` 中设置需要的 `stage`，运行 `DPmoireLite build config.yaml`，检查或提交生成目录；相关 VASP 作业完成后，再运行对应的 `collect`。

## Submission / 提交作业

`submit: false` only generates folders. `submit: true` generates folders and submits the relevant Slurm jobs, but returns after submission unless the command also receives `--wait`.

`submit: true` plus `DPmoireLite build config.yaml --wait` waits for submitted jobs where the workflow requires downstream outputs. `stage: all` requires both `submit: true` and `--wait`, because it must submit stage0, wait for the main dependency chain, and then generate stage1.

`submit: false` 只生成目录。`submit: true` 会生成目录并提交相关 Slurm 作业，但如果命令没有加 `--wait`，提交后就返回。

`submit: true` 配合 `DPmoireLite build config.yaml --wait` 会在流程需要下游输出时等待作业完成。`stage: all` 必须同时使用 `submit: true` 和 `--wait`，因为它需要先提交 stage0，等待主依赖链完成，再生成 stage1。

## Validation / 验证集

Validation is an independent timeline. Validation jobs do not block stage1, and validation data is collected only by:

```bash
DPmoireLite collect config.yaml --stage validation
```

验证集是独立时间线。validation 作业不会阻塞 stage1；验证集数据只会在用户显式运行 `collect --stage validation` 时收集。

## Stage1 Checks / Stage1 严格检查

Stage1 is intentionally strict. Before writing MD folders, it checks that each required relaxation source exists and is converged. When `vasp_ml: true`, it also checks that `init_mlff/ML_ABN` and `init_mlff/ML_FFN` exist. If any check fails, stage1 stops and reports all detected failures instead of generating a partial MD set.

Stage1 会严格检查输入。在写入 MD 目录前，它会检查每个必需的弛豫来源是否存在并已收敛；当 `vasp_ml: true` 时，还会检查 `init_mlff/ML_ABN` 和 `init_mlff/ML_FFN`。只要有检查失败，stage1 就停止，并报告所有检测到的问题，而不是生成不完整的 MD 集合。

## Collection / 数据收集

Collection is permissive. Missing, invalid, or unreadable sources are skipped where possible, and the stage manifest records collected frame counts plus skipped or failed records. This lets one bad calculation avoid destroying the whole collection pass.

Outputs are written separately:

- `rlx_data.extxyz` for `collect --stage rlx`
- `MD_data.extxyz` for `collect --stage md`
- `valid.extxyz` for `collect --stage validation`

DPmoire-lite does not automatically merge these files into one dataset.

收集逻辑是宽容的。缺失、无效或无法读取的来源会尽量跳过，并在对应阶段的 manifest 中记录帧数、跳过项和失败项。这样单个坏计算不会直接破坏整个收集过程。

输出文件保持分开：`rlx_data.extxyz`、`MD_data.extxyz` 和 `valid.extxyz`。DPmoire-lite 不会自动把它们合并成一个总数据集。

## Geometry Options / 几何参数

`n_sectors` controls stacking-sector sampling and accepts either an integer or a rectangular pair:

```yaml
n_sectors: 9       # same as [9, 9]
n_sectors: [9, 8]  # rectangular grid
```

`sc` controls supercell expansion and also accepts either an integer or a rectangular pair:

```yaml
sc: 2       # same as [2, 2]
sc: [2, 1]  # rectangular supercell
```

`n_sectors` 控制堆垛采样网格，可以写成整数或矩形二元组。`sc` 控制超胞扩展，也可以写成整数或矩形二元组。

## Init MLFF / 初始化 MLFF

Manual workflow: run stage0 with `init_mlff: true`, wait for the initial MLFF calculation to finish, make sure `ML_ABN` and `ML_FFN` are present in `init_mlff/`, then run stage1.

Automatic workflow: use `stage: all`, `submit: true`, and `DPmoireLite build config.yaml --wait`. DPmoire-lite submits the init MLFF and relaxation jobs, waits for the required dependency chain, prepares the second init MLFF step when needed, and then generates/submits stage1.

手动流程：设置 `init_mlff: true` 运行 stage0，等待初始化 MLFF 计算完成，确认 `init_mlff/` 中已有 `ML_ABN` 和 `ML_FFN`，再运行 stage1。

自动流程：使用 `stage: all`、`submit: true` 和 `DPmoireLite build config.yaml --wait`。DPmoire-lite 会提交 init MLFF 和弛豫作业，等待必需依赖链，在需要时准备第二步 init MLFF，然后生成/提交 stage1。

## Config Rules / 配置规则

Config keys use snake_case only. Old DPmoire field names are not accepted.

`encut_factor` multiplies the maximum `ENMAX` read from the selected POTCAR files to produce the rendered INCAR `ENCUT`.

配置键只接受 snake_case。旧版 DPmoire 字段名不会被接受。

`encut_factor` 的含义是：读取所选 POTCAR 中最大的 `ENMAX`，再乘以该系数，得到写入 INCAR 的 `ENCUT`。

## CLI / 命令

```bash
DPmoireLite init-example my_case
DPmoireLite build config.yaml
DPmoireLite build config.yaml --wait
DPmoireLite collect config.yaml --stage rlx
DPmoireLite collect config.yaml --stage md
DPmoireLite collect config.yaml --stage validation
```

