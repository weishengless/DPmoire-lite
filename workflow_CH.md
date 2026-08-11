# DPmoire-lite 工作流程

本文档详细说明 DPmoire-lite 从准备输入、生成 VASP 计算目录、提交 Slurm 作业到收集数据集的完整流程。配置项逐项说明见 `README_CH.md`。

## 1. 准备一个计算案例

先复制内置 example：

```bash
DPmoireLite init-example my_case
cd my_case
```

当前 example 使用：

- `config.yaml` 作为唯一工作流配置文件。
- `scripts/sub` 作为 Slurm 提交脚本，对应 `dft_script: sub`。
- `input/top_layer.poscar` 和 `input/bot_layer.poscar` 作为输入单层结构。
- `input/*_INCAR` 作为不同计算类型的 INCAR 模板。

正式运行前，至少需要修改：

- `potcar_dir`：指向真实集群上的 VASP POTCAR 根目录。
- `script_dir` 和 `dft_script`：确认提交脚本存在，并且适配当前集群环境。
- `input/top_layer.poscar` 和 `input/bot_layer.poscar`：替换为目标单层结构或已经匹配好的单层晶胞。
- INCAR 模板：根据体系调整泛函、MD 参数、MLFF 标签、资源和精度。
- `work_dir`：选择生成计算目录和数据集文件的位置。

example 中还包含一个已弃用的 `username` 项。DPmoire-lite 不使用它，它只作为旧工作流习惯的提示保留。

## 2. Stage0：生成初始计算

需要创建第一批计算目录时运行 stage0：

```bash
DPmoireLite build config.yaml
```

默认的非提交模式只写出目录，不运行 VASP。

stage0 可以根据配置生成三组相互独立的目录：

- `init_mlff: true` 时生成 `init_mlff/`；`init_mlff_mode` 决定使用历史单目录
  布局还是两阶段静态布局。
- `do_relaxation: true` 时生成 `rlx/<i>_<j>/` 堆垛弛豫目录。
- `twist_val: true` 时生成 `validation/<angle>/` twist validation 目录。

每个生成的计算目录会包含：

- `POSCAR`
- 渲染后的 `INCAR`
- `KPOINTS`
- `POTCAR`
- 配置指定的提交脚本
- 如果 INCAR 模板需要，则复制 `vdw_kernel.bindat`

写入任何计算目录前，preflight 会先解析 top layer 与 bottom layer 元素并集
所选中的 POTCAR 变体及其 ENMAX，并固定唯一的工作流 cutoff：
`ENCUT = encut_factor * max(selected ENMAX)`。init、relaxation、双层与单层
MD、validation 输入都会复用这个 ENCUT。每个目录的 POTCAR 仍是局部的：只包含
该目录 POSCAR 中的元素，并保持 POSCAR 元素顺序。各 stage manifest 会记录所选
POTCAR 目录名、ENMAX、控制元素、factor 和最终 ENCUT，但不会记录 POTCAR 内容。
Stage1 会把新解析的计划与新版 relaxation manifest 中的 cutoff 证据进行比较；
若发生漂移，会在写入 MD 前失败。缺少该证据的历史 manifest 仍可兼容使用，但会
明确警告并采用当前重新解析的计划。

如果任一目标 stage 已存在（包括空目录），DPmoire-lite 会在修改任何文件前停止。它不会移动、备份、覆盖或原地重建 stage。请显式删除完整的冲突 stage，然后重新运行 build。

## 3. Init MLFF 路径

初始 MLFF 目录用于提供 VASP-ML MD 计算需要的种子 `ML_ABN` 和 `ML_FFN`。

手动路径：

1. 设置 `stage: 0`、`init_mlff: true`，通常使用 `submit: false`。
2. 运行 `DPmoireLite build config.yaml`。
3. 在目标集群上手动提交或运行 `init_mlff/`。
4. 在 stage1 前确认 `init_mlff/ML_ABN` 和 `init_mlff/ML_FFN` 已经存在。

可审计的单 allocation 两阶段路径：

```yaml
stage: 0
submit: true
init_mlff: true
init_mlff_mode: single-job
init_bottom_incar: init_bottom_INCAR
init_top_incar: init_top_INCAR
```

该显式模式会在正式发布前一次性预检两层结构、两份科学 INCAR 模板、提交脚本
来源、所有选中的 POTCAR/ENMAX、共享工作流 cutoff 以及全部目标路径。随后分别生成
`init_mlff/bottom/` 和 `init_mlff/top/`。每个目录都从自身层结构和晶胞生成完整的
静态 VASP 输入；不会再用一层的 POSCAR、POTCAR 或依赖元素的 INCAR 字段覆盖另一层。
完整候选目录树和 manifest 会一起发布。

single-job 模式要求 Bash 提交模板把有效 `#SBATCH` 指令放在可执行内容之前，并使用
以下精确接口：

```bash
dpmoire_run_vasp() {
    # 必须同步运行并返回真实 VASP launcher exit code。
    srun vasp_std > "sout.${DPMOIRE_PHASE:-manual}"
}

dpmoire_run_vasp # DPMOIRE-LITE:RUN
```

函数声明、单独成行的结束 `}` 和带标记的调用行都是契约语法；launch function 定义和
marker 都必须恰好出现一次，且不能嵌套在其他 shell function 中。marker 必须是函数后
第一条顶层可执行语句，也是文件中最后一条可执行语句；其后只能有注释或空行。函数除
已 export 的环境和已安装命令外应当自包含，不能追加 `&`，也不能吞掉 launcher exit
status。DPmoire-lite 保证源模板字节不变，只替换派生 `init_mlff/<dft_script>` 副本中的
精确标记调用，不解析或改写任意 launcher、container、pipeline 或重定向文本。

当 `submit: true` 选择自动 single-job 路径时，模板不能包含 `#SBATCH --wait`、
`#SBATCH -W` 或含 `W` 的合法短选项组合；弃用的 `#SLURM -W` 写法以及每个
`hetjob`/`packjob` component 也会识别。preflight 会在创建目标工作目录之前拒绝
这些等待指令。由于 `sbatch` 可能翻译其他调度器语法，自动模式还会拒绝所有
模板任意位置以 `#PBS` 或 `#BSUB` 开头的内容；请改用原生 `#SBATCH`，或选择只生成
的 `submit: false`，后者会原样保留这些内容，供用户之后自行控制 `sbatch` 调用。

`init_mlff/manifest.yaml` 使用 `dpmoire-lite.init-workflow.v2`，把 bottom 记录为
`step-1-ready`、top 记录为 `planned`，并保存静态文件、源模板与派生 adapter 的
size/SHA-256 identity。这些证据可以识别后续 conflict，但不会暴露 POTCAR 内容或
其他私有 payload。

使用 `submit: false` 时，`DPmoireLite build config.yaml` 输出
`build status=generated`。检查并且只提交根目录下的派生脚本：

```bash
cd <work_dir>/init_mlff
sbatch <dft_script>
```

派生 adapter 会 export `dpmoire_run_vasp`，以 `SLURM_SUBMIT_DIR/..` 作为 work directory
并调用可信两阶段 workflow。必须按上例从派生脚本目录提交：Slurm 可能执行 spool 中的
脚本副本，因此运行时脚本路径不能作为可靠的 workspace 锚点；固定的提交目录相对路径
也允许整体移动 workspace。两个同步子进程
分别在 bottom/top 工作目录运行，环境中提供 `DPMOIRE_PHASE=step1|step2`，以及
`DPMOIRE_PHASE_ROLE`、`DPMOIRE_PHASE_NAME`。即使模板未使用 `set -e`，bottom 非零
退出或 MLFF 输出校验失败也会记录失败并阻止 top 启动；只有通过校验的 top 输出才会
发布根目录 `ML_ABN`/`ML_FFN`。

已完成的 `dpmoire-lite.init-workflow.v1` manifest 仍可读取，因此 Stage1 可以继续使用
此前已发布且通过 hash 校验的 seed；只有 v2 manifest 包含启动该自动两阶段命令所需的
submit-adapter 证据。

两次 VASP 启动共享同一个 Slurm allocation，因此资源必须同时适用于两步，walltime
必须覆盖两步总时长。

使用 `submit: true` 且不加 `--wait` 时，build 只提交这个根派生脚本一次。`sbatch`
成功后，它会把返回的 job ID 和脚本 identity 原子写入 `init_mlff/manifest.yaml`，输出
`build status=submission_requested`，然后退出；这个状态不表示计算已完成。`sbatch`
失败会返回非零退出码，并留下有界 `SUBMIT_FAILED` 证据，不会把 scheduler stderr
写进 manifest。这里不会第二次提交，也不会使用 `sacct`；不轮询调度器，不执行
retry、resume 或跨作业 dependency。
DPmoire-lite 还会从每次程序化 `sbatch` 的环境中移除继承的 `SBATCH_WAIT`，避免
父 shell 把该路径静默改成等待提交。

CLI 只有在至少一次 `sbatch` 请求成功时才输出 `submission_requested`。如果当前 stage
没有启用任何提交目标，即使配置为 `submit: true`，也会输出 `build status=generated`。

在 allocation 内，`run-init-mlff` 只有在两个 phase 校验通过并发布最终 seed 后才输出
`init_mlff status=complete`；workflow 失败时输出 `init_mlff status=failed` 并返回非零。

默认 `manual` 模式保留历史非等待提交路径：

```bash
DPmoireLite build config.yaml
```

当 `submit: true` 但不加 `--wait` 时，对于 init MLFF 流程只会提交第一步 init 作业。第二步 init MLFF 留给用户手动处理。stage0 仍会继续生成，并在启用时提交同一个配置中的弛豫和 validation 目录。

submitted `--wait` 当前暂时关闭。第一步 init 作业完成后，请检查输出，手动准备并提交第二步 init MLFF，并在进入 Stage1 前确认 `ML_ABN` 和 `ML_FFN`。

## 4. 弛豫网格

弛豫结构主要由 `n_sectors`、`sc`、`sc_rlx` 和 `symm_reduce` 控制。

`n_sectors` 定义堆垛平移采样网格：

```yaml
n_sectors: 9       # [9, 9]
n_sectors: [9, 8]  # 矩形网格
```

`sc` 定义超胞扩展：

```yaml
sc: 2       # [2, 2]
sc: [3, 2]  # 矩形超胞
```

如果 `sc_rlx: true`，弛豫目录中写入超胞堆垛结构。如果 `sc_rlx: false`，弛豫目录中写入 primitive glide structure；stage1 会把收敛后的 `CONTCAR` 扩展到 `sc`。

Stage1 默认清除 MD 约束。设置 `preserve_grid_shift_md: true` 只保留
DPmoire-lite 的网格平移锚点；其 `F F T` 掩码表示固定 x/y、允许 z 移动。
该选项不适用于任意用户约束。

如果 `symm_reduce: true`，DPmoire-lite 会用 `pymatgen`/`spglib` 对等价堆垛做对称性约化，并写出 `sym_reduced_stackings.txt`。

## 5. 提交 Stage0

设置 `submit: true` 后，生成目录会被 Slurm 提交。

自动 fire-and-forget 提交适用于两种 init 模式。默认 `manual` 模式提交历史单目录
init 作业；`single-job` 只提交一个根 adapter，由它在同一个 allocation 内运行两个
phase。希望先检查再手动提交 adapter 时，请使用 `submit: false`。

不加 `--wait` 时，DPmoire-lite 会提交当前 stage 请求的所有目录，然后退出：

```bash
DPmoireLite build config.yaml
```

这个 fire-and-forget 路径只保证 `sbatch` 调用成功，不保证作业最终成功，也不会自动推进依赖阶段。进程不会继续轮询 Slurm，因此无法执行 `n_nodes` 节流或 `auto_resub`。submitted `--wait` 当前暂时关闭，所以 `auto_resub` 不具备生产可用性。

## 6. Stage1：生成 MD 计算

弛豫作业完成后，设置：

```yaml
stage: 1
```

然后运行：

```bash
DPmoireLite build config.yaml
```

stage1 是严格检查的。在写入 MD 目录之前，它会检查所有必需的弛豫来源：

- 每个必需的 `rlx/<i>_<j>/OUTCAR` 必须存在，并包含 VASP 收敛短语；
- 每个必需的 `rlx/<i>_<j>/CONTCAR` 必须存在，并能被 ASE 读取；
- 如果 `vasp_ml: true`，`init_mlff/ML_ABN` 和 `init_mlff/ML_FFN` 必须存在。

只要有任何预检查失败，stage1 会一次性报告所有发现的问题，并且不会生成不完整的 MD 集合。

对于每个通过检查的弛豫来源，stage1 会从收敛后的 `CONTCAR` 写出 `md/<i>_<j>/POSCAR`。这个 POSCAR 会通过 ASE 重写，因此 VASP 弛豫输出末尾的速度块不会被带入 MD。这样 VASP 才能按目标温度重新初始化速度。

当 `vasp_ml: true` 时，stage1 会复制：

- `init_mlff/ML_ABN` 到每个 MD 目录中的 `ML_AB`
- `init_mlff/ML_FFN` 到每个 MD 目录中的 `ML_FF`

如果 `include_monolayer_md: true`，stage1 还会生成：

- `md/top_layer`
- `md/bot_layer`

## 7. 已关闭的 `stage: all`

由于 Slurm 终态校验和失败传播尚不可靠，`stage: all` 在所有 submit/wait 组合下都暂时不可用，也没有隐藏绕过方式。

请使用 `stage: 0` 和 `submit: false` 生成目录，手动分发和提交，并检查 init MLFF、弛豫以及可选 validation 输出。只有检查通过后，才使用 `stage: 1` 和 `submit: false` 生成 MD 目录，再手动提交并检查 MD 输出。

## 8. Validation 时间线

validation 独立于 rlx 到 MD 的主时间线。

当 `twist_val: true` 时，stage0 会在 `validation/` 下生成 twist validation 目录。这些计算不是 stage1 的输入，它们是否完成不会控制 MD 目录生成。

显式运行以下命令收集 validation 数据：

```bash
DPmoireLite collect config.yaml --stage validation
```

validation 收集会以频率 1 读取 OUTCAR，和 `outcar_collect_freq` 无关。

## 9. 收集数据集

只有在对应 VASP 计算完成后，才运行收集命令。

弛豫数据集：

```bash
DPmoireLite collect config.yaml --stage rlx
```

输出：

```text
work_dir/rlx_data.extxyz
```

MD 数据集：

```bash
DPmoireLite collect config.yaml --stage md
```

输出：

```text
work_dir/MD_data.extxyz
```

Validation 数据集：

```bash
DPmoireLite collect config.yaml --stage validation
```

输出：

```text
work_dir/valid.extxyz
```

CLI 只向 stderr 输出一行简短汇总；所选结果 manifest 保留详细的逐来源诊断。

| 退出码 | 状态 | 结果 |
| ---: | --- | --- |
| `0` | `complete` | 已发布帧，且预期来源覆盖完整。 |
| `1` | `fatal` | 配置、provenance、manifest、恢复或发布失败。 |
| `2` | `degraded` | 已发布帧，但覆盖存在 partial、skipped、failed 或未知。 |
| `3` | `no_data` | 没有发布新帧。 |

所有非零退出码都是 shell 失败。`no_data` 不会创建、删除或替换 extxyz；已有输出保持逐字节不变。

对于 `vasp_ml: true` 的 MD，默认 `seed-aware` 模式验证 Stage1 中不可变的 seed provenance，绝不会从当前 `md/ML_AB` 推断。旧版续算目录可显式运行：

精确 fast path 保持 `mlab-seed-v1` 不变。当 VASP 对可信 seed 前缀产生数值上可忽略的重写时，带版本的 fallback 要求元素、计数、原子顺序和 configuration 顺序完全一致，然后逐数值分量检查 `abs(a-b) <= 1e-12 * max(1, abs(a), abs(b))`。Manifest v2 fallback 只读取路径受限且通过原始哈希验证的 reference；legacy fallback 只读取完整的 `init_mlff/ML_ABN`，并发出兼容性警告。VASP-equivalent verification 视为完整覆盖，并且只排除一次 manifest 记录的 seed 数量。

```bash
DPmoireLite collect config.yaml --stage md --mlff-collect-mode full-dedup
```

`full-dedup` 读取每个可接受的最终 `ML_ABN`，对重复构型保留第一份精确副本（包括一份共享 seed），因此会产生更多 I/O。它只适用于 MLFF MD；用于弛豫、validation 或非 ML MD 时会 fatal 并返回 1。

当前 Manifest v2 始终具有最高权威。旧版或缺失 manifest 的兼容收集写入 `MD_data.collect.yaml`，不会伪造或重写 build provenance。缺失 manifest 扫描只允许 `full-dedup`；即使产生帧，由于预期来源覆盖未知，状态至多为 `degraded`。

对于非零 seed-aware 发布，`collect.dedup.seed_verification` 记录 `exact`、`vasp_equivalent` 和 `mismatch` 聚合计数。每个已验证来源在 `collect.sources[].seed_verification` 中记录有界的 identity、reference trust、最大 absolute/scaled delta，以及可选的首个 mismatch。完整 seed configuration 永远不会被序列化。精确 `mlab-seed-v1`、精确 `mlab-config-v1` 和显式 full-dedup 相互独立且保持不变。

对于弛豫和 `vasp_ml: false` 的 MD，DPmoire-lite 读取 OUTCAR 系列。默认 OUTCAR 匹配规则是：

```yaml
outcar_patterns:
  - '^OUTCAR\d+$'
  - '^OUT\d+$'
  - '^out\d+$'
  - '^OUTCAR$'
```

这覆盖了常见重启历史文件，例如 `OUTCAR0`、`OUT1` 和 `out2`，同时避免宽泛匹配到 `OUTCAR.bad` 这类文件。

## 10. 常见手动工作流

对于手动分段、多集群计算，可以使用：

```bash
DPmoireLite init-example my_case
cd my_case

# 修改 config.yaml 和输入文件。
# stage: 0, submit: false
DPmoireLite build config.yaml

# 手动提交并完成 init_mlff 和 rlx 计算。
# 确认 init_mlff/ML_ABN、init_mlff/ML_FFN、rlx/*/OUTCAR 和 rlx/*/CONTCAR 存在。

# stage: 1, submit: false
DPmoireLite build config.yaml

# 手动提交并完成 MD 计算。
DPmoireLite collect config.yaml --stage rlx
DPmoireLite collect config.yaml --stage md
```

只在需要时单独收集 validation：

```bash
DPmoireLite collect config.yaml --stage validation
```
