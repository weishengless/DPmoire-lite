# Array Submission Scripts（用户批准设计，2026-09-13）

- 状态：已实现（`array_submission` / `array_max_concurrent`）
- 动机：`rlx/`、`md/` 按用户 grid 与对称约化生成大量计算目录，逐目录
  `sbatch` 会在队列中产生大量独立排队条目。

## 行为契约

- `array_submission: true` 时，在 `rlx/` 与 `md/` stage 根生成
  `array_<dft_script>`；`init_mlff` 与 `validation` 排除。
- 生成脚本 = 用户 `dft_script` 模板全文 + 注入：
  1. `#SBATCH --array=0-N[%K]`（`%K` 仅当 `array_max_concurrent > 0`）；
  2. `#SBATCH -o %x.%A.%a.out` / `-e %x.%A.%a.err` 覆盖行，防止 array 任务
     互相覆盖用户模板中的 `-o out.%j` 类输出；
  3. 内嵌 `ARRAY_FOLDERS` 列表与按 `SLURM_ARRAY_TASK_ID` 的 `cd`，
     `SCRIPT_DIR` 先定位脚本所在目录，因此从任意 CWD 提交均成立；
     任务开始时向输出文件 echo 一行 task → 目录映射便于排查。
- 用户模板正文（module / 环境变量 / mpirun）逐字保留；各目录内的原脚本
  拷贝不受影响。
- 每层一个 array 任务即一个完整节点作业；`>sout` 等 body 内重定向发生在
  各计算目录内，无冲突。

## 安全边界

- 只生成，不提交：`submit: true` 的逐目录提交通道保持不变，本功能不新增
  任何 sbatch 调用，不触碰被禁用的 `--wait` / 轮询语义。
- 失败封闭：模板无 `#SBATCH` 头、目录列表为空、`SLURM_ARRAY_TASK_ID`
  未设置（未以 array 方式提交）时分别显式报错。
- 写入发生在 stage 目录 one-shot 认领期内（原子发布）；生成脚本的路径与
  sha256 记入 stage manifest 的 `array_scripts` 字段（已加入
  `_MANIFEST_FIELDS` 白名单）。

## 测试

- `tests/test_slurm.py`：渲染器注入/限流/缺 SBATCH/空列表 4 例。
- `tests/test_config.py`：缺省关闭、接受配置、拒绝负值 3 例。
- `tests/test_build.py`：stage0 开启（含 manifest `array_scripts` 读回）与
  缺省不生成 2 例；md 目录列表（含 top_layer/bot_layer）1 例。
