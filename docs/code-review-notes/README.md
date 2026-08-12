# DPmoire-lite Code Review Follow-up Notes

Date: 2026-07-11

本目录记录 `docs/2026-07-11-code-review.md` 后续讨论形成的准确问题边界、
问题来源、已确认解决方案和未来实现验收条件。原评审中的概括如与本目录
冲突，以对应 follow-up note 为准。

当前方案设计已经讨论确认，生产代码实现尚未开始。唯一已执行的实施前修正
是将本地大型 `example-test/` 加入 `.gitignore`，防止误提交。

## 当前范围

团队目前主要使用 DPmoire-lite：

1. 分阶段生成计算目录；
2. 手动分发到不同集群并提交；
3. 汇总和收集训练数据。

因此本轮优先处理目录生成和数据收集的科学正确性。Slurm 无人值守自动化、
MD restart 自动化、CI matrix 和 lint/type-check 基础设施已单独暂缓。

## 已确认问题

| ID | 问题 | 结论摘要 | Note |
| --- | --- | --- | --- |
| P0 | 仓库卫生 | `example-test/` 整体忽略，只裁剪可再分发 fixture，POTCAR 不入库 | [P0](P0-repository-hygiene.md) |
| P1-1 | MD Selective Dynamics | 默认清除全部约束；保留模式只接受 manifest 记录的两个项目锚点 | [P1-1](P1-1-md-selective-dynamics.md) |
| P1-2 gate | 自动化安全门 | 完整 Slurm 修复前 fail-closed `stage: all` 和 submitted `--wait` | [P1-2 gate](P1-2-automation-safety-gate.md) |
| P1-4 | INCAR 安全改写 | 只控制 ENCUT/ML_RCUT；安全解析合法语法；重复冲突预检 | [P1-4](P1-4-incar-safe-rendering.md) |
| P1-5 | Stage provenance | 新 manifest 严格校验；旧 manifest 以原子数和晶胞双判据兼容 | [P1-5](P1-5-stage-provenance.md) |
| P1-6 | MD 初始速度 | Stage1 无条件删除输入速度；MD restart 另行设计 | [P1-6](P1-6-md-initial-velocities.md) |
| P2-1 | 截断来源与 MLFF seed | 默认 canonical digest 复核 seed prefix；可选 full-dedup 全量 exact 去重 | [P2-1](P2-1-partial-collection-and-mlff-seed.md) |
| P2-2 | Stage rebuild | stage 一次性生成；任一目标已存在则整条 build 命令 fail-closed | [P2-2](P2-2-stage-rebuild-contract.md) |
| P2-3 | 收集输出安全 | 旧正式文件原位，备份先提交；candidate/manifest 由 transaction journal 协调 | [P2-3](P2-3-collection-output-safety.md) |
| P2-4 | OUTCAR patterns | 严格 YAML regex list；pattern priority + natural sort，禁用 mtime | [P2-4](P2-4-outcar-pattern-validation.md) |
| P2-5 | OUTCAR 内存峰值 | 打开文件句柄生命周期内直接迭代专用 iterator，并验证首帧 lazy | [P2-5](P2-5-outcar-streaming.md) |
| P2-6 | collect 退出码 | 0 complete、1 fatal、2 degraded、3 no_data；显式 legacy scan 至多 degraded | [P2-6](P2-6-collect-exit-codes.md) |

## 暂缓问题

| 原评审项 | 暂缓内容 | Note |
| --- | --- | --- |
| P1-2 完整修复 | Slurm 最终失败阻断、重试和退出码；当前由 safety gate 关闭 | [Slurm automation](deferred-slurm-automation.md) |
| P1-3 | `sacct` 无截断解析和状态分类 | [Slurm automation](deferred-slurm-automation.md) |
| P2-2 自动化部分 | job ID、状态和 workflow manifest 增量持久化 | [Slurm automation](deferred-slurm-automation.md) |
| Future | 从旧 MD CONTCAR 保留速度的 restart 工作流 | [Slurm automation](deferred-slurm-automation.md) |
| Tooling | CI matrix、lint/type-check、Slurm 集成 fixture | [Testing scope](testing-and-tooling-scope.md) |

## 测试范围

本轮必须使用小型可再分发 fixtures 覆盖上述功能，并补充
`find_sym_reduced_stackings()` 直接测试。完整范围见
[testing-and-tooling-scope.md](testing-and-tooling-scope.md)。

## 关键交叉依赖

```text
P0 repository hygiene ────────────────> 所有 fixture/打包步骤

P1-4 INCAR/config 预检 ─┐
P1-5 provenance ────────┼─> P2-2 build 入口保护
P1-1 constraints ───────┤
P1-6 velocities ────────┘

P1-2 safety gate ─────────────────────> 暂缓 Slurm 自动化实现

P2-4 OUTCAR 发现 ───────┐
P2-5 OUTCAR 流式读取 ───┼─> P2-1 来源解析与 partial 状态
MLFF initial seed ──────┘             │
                                     v
                          P2-3 数据集安全发布 ──> P2-6 CLI 状态
```

## 进入实现前

在开始功能设计和实现前，应由用户确认：

- 本目录已经覆盖本轮希望处理的问题；
- 暂缓项无需进入当前实现；
- 各 note 的解决方案没有遗漏实际集群工作流；
- 可以按交叉依赖拆分设计和实施批次。
