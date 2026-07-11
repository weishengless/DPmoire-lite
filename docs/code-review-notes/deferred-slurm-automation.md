# 暂缓专题：Slurm 自动提交与无人值守工作流

- 状态：已从当前修复范围中切出，留待后续自动化开发
- 决定日期：2026-07-11
- 来源：`docs/2026-07-11-code-review.md` 中的 P1-2、P1-3，以及 P2-2 的提交持久化部分

## 范围决定

团队当前主要使用 DPmoire-lite 完成以下工作：

1. 按阶段生成计算目录；
2. 用户自行将目录分发到不同集群并提交任务；
3. 计算完成后汇总和收集数据。

因此，当前优先处理的是计算目录生成和数据收集流程的正确性。以下自动化
能力暂不进入本轮功能设计和实现：

- `stage: all` 无人值守依赖链；
- `--wait` 轮询与并发节流；
- Slurm 失败状态判定和自动重投；
- 作业失败后的依赖阶段阻断；
- Slurm job ID 和最终状态的增量持久化；
- 自动化工作流的成功/失败退出码。

这些问题仍然成立，本次范围调整不代表问题已解决。

虽然完整自动化延期，本轮必须实现
[`P1-2-automation-safety-gate.md`](P1-2-automation-safety-gate.md)：在本 note
的重新开放条件满足前，`stage: all` 和 submitted `--wait` 工作流 fail-closed，
不能作为正式功能继续暴露。

## A. 最终失败的 Slurm 作业不会可靠阻断后续阶段

### 问题来源

- `src/dpmoire_lite/slurm.py:85-126`
- `src/dpmoire_lite/build.py:170-177`
- `src/dpmoire_lite/build.py:230-259`

`SlurmRunner.submit_many(wait=True)` 会正常返回状态为 `FAILED` 的作业，
调用方没有要求每个计算目录的最后一次尝试必须为 `COMPLETED`。

这可能造成：

- init step1 失败后，只要目录中残留 `ML_ABN` 和 `ML_FFN`，仍继续准备并
  提交 step2；
- 自动重投后的最终失败仍可能放行依赖阶段；
- validation 或最终 MD 作业失败时，CLI 仍可能正常退出；
- 部分输出文件的存在性检查掩盖真实的 Slurm 失败状态。

Stage1 的弛豫收敛预检可以拦截部分坏输出，但它不是 Slurm 成功状态检查的
替代品。

### 后续建议方案

后续开发自动化时，建议采用“完成当前独立批次、聚合全部失败、阻止后续
依赖阶段”的策略：

1. 按计算目录归并原始提交和重试作业；
2. 只把每个目录最后一次尝试的 `COMPLETED` 视为成功；
3. `auto_resub` 最多重试一次；
4. 当前批次的独立计算全部收尾后，汇总最终失败目录；
5. 先持久化所有尝试和最终状态，再抛出聚合错误；
6. 不再启动任何依赖阶段，并让 CLI 返回非零。

该策略尚未进入正式功能设计，后续开发时仍需确认失败批次是否继续提交尚未
进入队列的独立目录。

## B. `sacct` 输出和状态分类不可靠

### 问题来源

- `src/dpmoire_lite/slurm.py:9-26`
- `src/dpmoire_lite/slurm.py:46-55`
- `src/dpmoire_lite/slurm.py:74-83`
- `src/dpmoire_lite/slurm.py:110-140`

当前查询使用默认宽度的 `JobID,State`。Slurm 可能使用 `+` 截断较长状态，
例如：

```text
REQUEUE_HOLD  -> REQUEUE_H+
OUT_OF_MEMORY -> OUT_OF_ME+
```

实现又使用精确字符串集合进行判断。被截断或未列出的状态可能被错误地当成
终态，从而提前释放并发槽位、跳过自动重投，或者错误结束等待。

### 后续建议方案

1. 使用无截断、机器可读的查询格式，例如
   `--noheader --parsable2 --allocations --format=JobIDRaw,State%30`；
2. 解析前去除附加状态文本并规范化字段；
3. 明确定义成功终态、失败终态和活动状态；
4. 未知状态不得按成功或已完成处理，应保持活动/不确定或抛出可诊断错误；
5. 覆盖 Slurm 实际可能返回的 held、requeued、configuring、stage-out、
   signaling、preempted、out-of-memory 等状态；
6. 为真实 `sacct` 输出增加固定测试样本。

状态分类是上一节失败阻断策略的前置依赖，后续应一起设计和实现。

## C. 提交记录和自动化 manifest 更新不具备事务性

### 问题来源

- `src/dpmoire_lite/build.py:89-135`
- `src/dpmoire_lite/build.py:230-259`
- `src/dpmoire_lite/build.py:272-321`
- `src/dpmoire_lite/build.py:438-445`

manifest 通常在整个生成和提交过程结束后才写入。如果中间某次 `sbatch`
失败，已经成功提交的 job ID 可能没有持久化；重新执行又可能重复提交。

### 后续建议方案

1. 每次 `sbatch` 成功后立即持久化 job ID；
2. 每次状态轮询或终态变化后原子更新 manifest；
3. manifest 记录工作流状态，例如 `generating`、`submitted`、`waiting`、
   `failed` 和 `completed`；
4. 重启工作流时根据持久化状态恢复，而不是盲目重复提交；
5. 将目录生成事务和作业提交事务分开，避免文件准备失败与集群状态混为一体。

P2-2 中“计算目录生成不原子”的部分不在本暂缓专题内，仍属于当前重点，
因为即使完全手动提交，部分生成或错误备份也会直接影响计算输入可靠性。

## 当前工作流建议

在上述问题实现前：

- 以 `submit: false` 分阶段生成目录；
- 由用户在各集群手动提交并检查作业状态；
- 仅在确认相应阶段输出完整后进入下一阶段；
- 当前 safety gate 直接拒绝 `stage: all` 和 submitted `--wait`；
- 数据收集前先确认计算完成，后续再由收集流程执行内容级校验。

## 恢复自动化开发时的验收方向

- 每个目录的最终尝试必须明确归类为成功或失败；
- 一个依赖阶段只在全部前置目录成功后启动；
- 最终失败必然产生非零 CLI 退出码；
- 未知或截断状态不能被错误视为完成；
- job ID 和状态在进程异常退出后仍可恢复；
- 自动重投不会对同一目录超过配置次数；
- 自动化测试覆盖批量失败、部分失败、重试失败和进程恢复。

## D. MD restart 工作流

### 范围来源

P1-6 已确认 Stage1 始终表示“从弛豫后的稳定结构开始一条新的 MD 轨迹”，
因此必须删除输入 CONTCAR 的速度，让 VASP 按 `TEBEG` 初始化。

从旧 MD 的 CONTCAR 续算则具有相反需求：为了保持轨迹连续性，需要保留旧
MD 的速度。这种行为不能通过普通 Stage1 的隐式例外实现，应作为独立
restart 工作流留待未来自动化开发。

### 后续建议方向

未来设计 restart 时，应显式校验和处理：

- 输入确实来自 MD，而不是结构弛豫；
- CONTCAR 中存在与原子数一致的有效速度区块；
- 是否需要继承晶格速度和可变胞状态；
- INCAR 中温控器、`TEBEG`、`TEEND`、`MDALGO`、`SMASS` 等参数是否与
  续算语义兼容；
- MLFF 文件、WAVECAR、随机数状态或其他连续运行文件的继承规则；
- restart 与新轨迹在目录名、manifest 和 CLI 中具有不可混淆的入口。

当前没有进一步开发该功能的计划，只记录需求边界，不进入本轮设计与实现。
