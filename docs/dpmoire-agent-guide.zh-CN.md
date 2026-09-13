# 用 Agent 学习和操作 DPmoire-lite

仓库内置的 `dpmoire-guide` skill 把任务分成四种模式，并给每种模式规定
证据、权限和停止条件。它的目标不是替 Agent 记住整套代码，而是让上下文较短、
推理能力较弱的模型也能每次回到当前仓库事实，不靠猜测继续工作。

## 1. 如何调用

在 Codex 中，从本仓库目录打开任务，然后输入：

```text
$dpmoire-guide [LEARN] 用初学者能懂的方式解释 Stage0 到 Stage1 的数据流。
```

也可以先用 `/skills` 查看可用 skill。支持 Skills 的 ChatGPT 客户端使用
`@dpmoire-guide`。如果其他 Agent（包括不支持 Codex skill 协议的模型）没有自动
发现它，就把 `.agents/skills/dpmoire-guide/` 整个目录作为上下文提供，并明确说：

```text
先完整阅读 SKILL.md，只加载当前模式对应的 reference，然后再处理请求。
```

不要猜某个第三方 Agent 的专用调用语法；不支持 skill 时，直接附加文件最可靠。
对于 DeepSeek V4 Flash 等没有接入仓库 skill 自动发现的模型，也使用这个显式附加
目录的方法，并保留四种 task tag，不依赖它猜测工作流。

skill 内的 `scripts/` 和 `references/` 都相对于
`.agents/skills/dpmoire-guide/`，不是相对于 Agent 当前的 shell 目录。例如快照脚本
的完整仓库内路径是
`.agents/skills/dpmoire-guide/scripts/repo_snapshot.py`。

如果新建或修改 skill 后没有显示，确认当前目录位于仓库内，再检查 `/skills`；
仍未出现时重启客户端。

## 2. 四个任务 tag

| Tag | 用在什么任务 | 默认是否允许写入 |
| --- | --- | --- |
| `[LEARN]` | 解释概念、画数据流、带读代码 | 否 |
| `[RUN]` | 用户已授权的生成、收集或提交操作 | 只允许请求中明确授权的动作 |
| `[DEBUG]` | 查明错误原因，但不修复 | 否 |
| `[CHANGE]` | 修改代码、测试、文档或配置 | 只允许请求范围内的文件 |

tag 只是帮助 Agent 选流程，不是权限开关。写了 `[RUN]` 也不会自动授权提交 Slurm
任务或删除目录；这些高影响动作仍要在请求中说清楚。

最实用的 prompt 模板：

```text
$dpmoire-guide [LEARN]
目标：解释 manifest 为什么是 Stage1 和 collect 的权威来源。
要求：只读；给出代码入口和一个代表性测试；不要运行计算。
```

```text
$dpmoire-guide [RUN]
目标：只生成 Stage0 目录，不提交任务。
配置：<config.yaml 的绝对路径>
权限：允许读取配置并执行 build；不允许删除已有 stage，不允许 sbatch。
停止条件：打印 build 状态并列出实际新增路径后停止。
```

```text
$dpmoire-guide [DEBUG]
命令：<原命令>
现象：collect status=no_data，exit 3。
目标：只诊断为什么没有数据；保护已有 extxyz、manifest、lock 和 journal。
```

```text
$dpmoire-guide [CHANGE]
目标：修复 <可观察行为>。
依据：Issue/计划链接或本地文件。
要求：先建立预期 RED，再做最小改动并跑 GREEN；不提交、不 push。
```

## 3. “tag”有三种含义

1. `$dpmoire-guide`：Codex 中的 skill 调用名。
2. `[LEARN]`、`[RUN]`、`[DEBUG]`、`[CHANGE]`：这套 skill 的任务路由 tag。
3. `stage`、`submit`、`vasp_ml` 等：`config.yaml` 的配置键，README 把它们称为
   Config Tags。

配置键的完整含义以当前 `README.md` 的 **Config Tags** 表和
`example/config.yaml` 注释为准。常用分组如下：

- 路径与资源：`dft_script`、`potcar_dir`、`potcar_policy`、`script_dir`、
  `input_dir`、`work_dir`、`n_nodes`。
- 执行控制：`stage`、`submit`、`auto_resub`。
- 计算与收集：`vasp_ml`、`outcar_collect_freq`、`outcar_patterns`。
- 工作流：`do_relaxation`、`init_mlff`、`init_mlff_mode`、
  `init_bottom_incar`、`init_top_incar`、`sc_rlx`、`include_monolayer_md`。
- 结构与采样：`n_sectors`、`sc`、`d`、`d_mode`、`d_reference`、`k_mesh`、
  `encut_factor`、`r_cut`、`symm_reduce`、`twist_val`、`min_val_n`、
  `max_val_n`、`preserve_grid_shift_md`、`grid_shift_anchor`、`array_submission`、
  `array_max_concurrent`。

所有配置键使用 snake_case。`VASP_ML`、`K-mesh`、`POTCAR_dir`、
`DFT_script`、`ENMAX`、`OUTCAR_collect_freq` 等旧写法会被拒绝。

## 4. 最小工作流

1. `DPmoireLite init-example my_case` 复制示例。
2. 编辑配置，先用 `stage: 0`、`submit: false` 生成 Stage0。
3. 人工检查并完成 init、relaxation 和可选 validation 计算。
4. 确认 relaxation 的 `CONTCAR`；MLFF MD 还要确认 `init_mlff/ML_ABN` 和
   `ML_FFN` 的权威证据。
5. 改为 `stage: 1`、`submit: false`，生成 MD 目录。
6. 完成 MD 后，分别运行 `collect --stage rlx|md|validation`。

`stage: all` 当前被刻意禁用；`submit: true --wait` 也被刻意禁用。
`submission_requested` 只表示 `sbatch` 接受了请求，不表示 VASP 运行成功。

## 5. 常见错误怎么理解

| 现象 | 正确含义 | 新手应做什么 |
| --- | --- | --- |
| `Build target conflict(s)` | 目标 stage 已存在，空目录和 symlink 也算 | 停止并报告完整路径；不要让 Agent 自动删除或备份 |
| `Build preflight failed` | 输入无法安全生成；应尚未创建计算目录 | 逐项修配置/输入，不要只处理最后一条错误 |
| `build status=generated` | 只生成了文件 | 检查目录和脚本，不要说“计算完成” |
| `build status=submission_requested` | Slurm 接受了至少一个请求 | 到调度器核实真实 job 结果 |
| `submission_failed` / `SUBMIT_FAILED` | 提交失败，manifest 留有受限证据 | 保留证据，诊断脚本/调度器，再决定是否重试 |
| collect exit `1` / `fatal` | 配置、provenance、manifest、恢复、锁或发布失败 | 不要盲目重跑；先按首个 fatal 原因分层诊断 |
| collect exit `2` / `degraded` | 数据集已经发布，但覆盖不完整或未知 | 保留输出并报告缺失/失败 source，不能称为 complete |
| collect exit `3` / `no_data` | 没有发布新 frame | 旧 extxyz 应保持原字节；检查声明目录和匹配规则 |
| `full-dedup ... requires MLFF MD` | 模式用在了 rlx、validation 或非 ML MD | 改用默认 seed-aware，或核对 MLFF MD 配置 |
| manifest 缺失 | 当前权威证据不存在 | 不要手工伪造；只有显式 MD full-dedup 有受限兼容扫描 |
| lock/journal/recovery 错误 | 并发写入、中断事务或目标路径变化 | 停止其他 writer，保留 lock/journal，再确认所有者和目标 |

## 6. 给低上下文模型的验收标准

每次回答至少应包含：

- 它选择的 mode、目标、允许写入范围、停止条件；
- 实际读取的权威文件；
- 实际运行的命令、工作目录、解释器、退出码和状态；
- 实际改动路径，或明确写 `none`；
- 哪些目录、dataset、manifest、lock、journal 被保留；
- 一个安全的下一步，而不是一次跳过多个阶段。

如果回答只说“看起来没问题”“应该成功”，没有这些证据，就不要让它继续执行。

## 7. Python、测试和私有数据

当前仓库的 `AGENTS.md` 指定了唯一可依赖的 Python。Agent 必须先验证
`sys.executable`，不能擅自换成 bare `python`、`python3` 或 WindowsApps alias。
如果那个路径在新机器上不存在，应先由维护者更新本机执行约定，而不是静默绕过。

自动测试不能直接依赖 `example-test/`。POTCAR 内容和私有计算输出不能进入 prompt、
fixture、包、暂存区或提交。Agent 可以报告文件是否存在、hash 或受限 provenance，
但不应复制其内容。
