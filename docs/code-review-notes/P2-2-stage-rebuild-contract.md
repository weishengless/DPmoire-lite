# P2-2：build 隐式原地重建与项目使用约定不一致

- 状态：讨论已完成
- 评审日期：2026-07-11
- 结论日期：2026-07-11
- 优先级：P2
- 关联位置：
  - `src/dpmoire_lite/paths.py:22-53`
  - `src/dpmoire_lite/build.py:73-135`
  - `src/dpmoire_lite/build.py:230-332`
  - `src/dpmoire_lite/build.py:429-435`

## 原评审问题

原评审将目录生成、备份、提交和 manifest 更新不原子归为同一个 P2：程序
在完成全部输入验证前先移动旧目标，然后逐个在正式 stage 目录中生成新
文件。如果中间失败，正式目录可能出现部分新内容、旧 manifest 或缺失
目录。

该描述对当前代码成立，但最初建议的整 stage staging、原子替换和自动恢复
不符合团队实际使用方式，属于过度设计。

Slurm 提交、job ID 持久化和无人值守恢复已经单独移入
`deferred-slurm-automation.md`。本 note 只讨论 `submit: false` 的计算目录
生成。

## 已确认的实际工作流

团队不要求 DPmoire-lite 在已有 stage 上执行原地 rebuild。实际约定是：

```text
首次 build
    -> 检查生成目录
    -> 手动分发和提交

如果 input 有误、生成被 Ctrl+C 中断或需要 rebuild
    -> 用户删除整个目标 stage
    -> 修正 input/config
    -> 重新 build
```

因此，中断后留下半成品并不是需要自动恢复的持久状态。用户会整体删除它，
而不是要求程序判断哪些 stacking 可以复用。

## 当前代码与约定的冲突

当前 build 在目标目录已存在时，会逐个调用
`backup_existing_directory()`：

1. 将已有 `rlx/<stacking>` 或 `md/<stacking>` 移到 backup；
2. 在原正式路径逐个生成新目录；
3. 全部结束后才写 manifest。

这实际上隐式支持“原地 rebuild”。如果已有 stage 包含完成计算，误运行
build 后又中断，正式 stage 可能只剩部分新目录，旧结果则分散在多个
per-target backup 中。

数据通常没有立即丢失，但行为与用户“需要重建就先整体删除”的预期不同，
也增加了误操作和恢复复杂度。

## 已确认的解决方案

### 禁止原地 rebuild

每个 stage 被视为一次性生成物：

- `init_mlff`；
- `rlx`；
- `validation`；
- `md`。

build 开始时，如果本次任一目标 stage 已经存在，立即停止，不移动、不
备份、不覆盖任何内容。即使目录为空，也要求用户显式删除，避免把上次中断
或人工创建的路径误认为安全目标。

示例：

```text
work/md already exists.
DPmoire-lite does not rebuild a stage in place.
Remove work/md and rerun stage1.
No files were modified.
```

检查必须在创建新目录、更新 root 辅助文件或进行任何 backup 之前执行。

### Stage0 多目标全局入口检查

Stage0 可以同时启用 `init_mlff`、`do_relaxation` 和 `twist_val`。程序必须先
根据 config 计算本次全部目标 stage 集合，例如：

```text
[init_mlff, rlx, validation]
```

随后在生成任何一个 stage 前一次性检查全部目标：

- 任一目标已存在，聚合列出所有冲突并终止；
- 不能先生成 `init_mlff`，之后才发现 `rlx` 已存在；
- 不能先写 `sym_reduced_stackings.txt` 或其他 root artifact；
- 所有目标存在性检查通过后，才进入跨 stage 的输入预检；
- 所有目标输入预检通过后，才允许创建第一个 stage。

如果用户只希望生成尚不存在的 rlx，应在 config 中关闭本次不需要的
`init_mlff`/`twist_val`，并确保目标 `rlx` 已显式删除。

### 用户显式删除

需要重建时，用户负责显式删除整个 stage 目录。删除动作不由普通 build
隐式执行，也不增加自动 `--force` 或自动 cleanup 行为。

这种约定具有明确授权边界：只有用户确认旧 stage 不再需要后，才会删除它。

### 首次生成中断

如果首次 build 过程中发生 Ctrl+C 或写出错误：

- 半成品 stage 可以保留供诊断；
- manifest 只在全部目录成功生成后发布；
- 用户删除整个半成品 stage 后重新运行；
- 程序不尝试复用部分 stacking；
- 不增加 `.build_incomplete` 自动恢复状态机。

### 生成前预检

虽然不实现 stage 事务，仍应在创建目标 stage 前完成所有能够确定的检查，
减少用户删除和重跑次数：

- config 完整性；
- P1-4 INCAR 解析、重复标签和正则预检；
- 所需模板和 submit script；
- 所有元素的 POTCAR 解析；
- vdW kernel 依赖；
- 输入结构可读性；
- P1-5 Stage1 provenance；
- 全部 relaxation OUTCAR/CONTCAR 前置条件；
- ML_ABN、ML_FFN 非空、seed configuration count 和 canonical digest；
- 目标路径位于预期 work_dir 内。

预检只能减少可预见失败，不能替代用户处理中断后的整体删除约定。

### manifest 完成语义

- stage manifest 仍在所有目录和必要文件成功写出后生成；
- manifest 缺失表示 stage 未被 build 确认为完整；
- manifest 本身采用临时文件和原子替换，避免半个 YAML；
- collect 或后续 Stage1 不能把“目录存在但 manifest 缺失”自动视为完整
  stage；
- Stage1 必须删除当前从 config、`sym_reduced_stackings.txt` 或目录扫描推导
  stacking 的隐式 fallback；
- collect 必须删除当前 `read_manifest(...) or _new_manifest(...)` 以及从
  config 推导目录的隐式 fallback；
- 缺少 manifest 时，Stage1/collect 以明确错误退出，并说明该 stage 可能是
  中断产生的半成品；
- P1-5 定义的旧格式兼容仅适用于“manifest 文件存在，但缺少新版
  provenance 字段”的历史工作流；
- 旧格式兼容不适用于完全没有 manifest 的目录，也不能通过辅助文件重新
  创造一个 manifest。

## 不采用的复杂方案

当前不实现：

- 整 stage staging 和原子目录替换；
- per-target 事务提交；
- Ctrl+C 自动回滚；
- 自动识别并复用完整 stacking；
- 自动删除半成品；
- 隐式覆盖或 `--force` rebuild；
- build 中断恢复状态机。

这些能力只有在未来项目明确需要自动化原地重建时才重新设计。

## 与数据集备份的区别

P2-3 已确认 collect 替换 extxyz 前应创建时间戳备份。该决定不意味着 build
stage 也要支持原地重建：

- extxyz 是反复运行 collect 时更新的汇总产物，需要版本恢复；
- 计算 stage 是一次性生成目录，需要重建时用户整体删除；
- 两者生命周期和授权语义不同，应采用不同策略。

## 后续实现的验收条件

以下仅记录未来实现要求，本 note 不包含代码修改：

- 任一目标 stage 已存在时，build 在任何修改前失败，空目录也不例外；
- Stage0 同时启用多个目标时先检查全部目标；一个冲突会阻止其他目标生成；
- 错误信息明确要求用户删除整个 stage 后重跑；
- 失败检查不会自动移动已有计算结果到 per-target backups；
- 不存在的目标 stage 可正常首次生成；
- 所有可预检输入在创建 stage 前完成检查；
- 首次生成中断后，删除整个 stage 即可重新 build；
- manifest 只代表完整成功生成的 stage；
- manifest 写出是原子的；
- 无 manifest 的半成品不会被后续流程误认为新格式完整 stage；
- Stage1 不再从 config 或 `sym_reduced_stackings.txt` 隐式恢复 stacking；
- collect 不再自动创建 manifest 或从 config 推导来源目录；
- 只有存在旧 manifest 时才能进入 legacy compatibility；
- init_mlff、rlx、validation 和 md 都遵循同一存在性规则；
- 测试覆盖已有非空 stage、首次生成、生成中断模拟、删除后重跑和预检失败
  不修改目标目录。
- 测试覆盖空目标目录，以及 `init_mlff/rlx/validation` 中一个已存在时另外
  两个也完全不生成。
