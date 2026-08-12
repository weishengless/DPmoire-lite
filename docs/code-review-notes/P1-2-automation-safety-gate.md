# P1-2 Safety Gate：不可靠自动化修复前必须 fail-closed

- 状态：讨论已完成，属于本轮必须实现的安全门
- 决定日期：2026-07-11
- 优先级：P1
- 关联问题：P1-2、P1-3 和 deferred Slurm automation

## 问题

Slurm 最终失败传播、状态解析、自动重投和 job manifest 持久化已延期。但
当前 CLI 仍正式开放 `stage: all --wait`，用户可能把已知不可靠的自动化路径
用于生产。

延期实现不等于允许继续暴露一个会在失败作业后返回成功、放行依赖阶段或
错误解释 `sacct` 状态的入口。

## 已确认的临时安全契约

在 `deferred-slurm-automation.md` 的完整验收条件实现前：

- `stage: all` 无条件拒绝执行；
- `submit: true` 与 `--wait` 的组合拒绝执行，包括独立 stage0/stage1；
- 不提供隐藏环境变量或未记录的绕过方式；
- 错误信息明确说明功能因已知可靠性问题暂时关闭；
- 推荐使用 `submit: false` 生成目录后手动提交；
- `submit: true` 且不等待的 fire-and-forget 入口可保留，但必须明确只保证
  `sbatch` 调用成功，不保证作业最终成功，也不自动推进依赖阶段；
- `auto_resub` 在 wait 被关闭期间不宣称可用于生产。

示例错误：

```text
stage: all and submitted --wait workflows are temporarily disabled because
Slurm terminal-state validation and failure propagation are not yet reliable.
Generate stages with submit: false and submit them manually.
```

## 重新开放条件

只有完成以下全部项目才能删除 safety gate：

- 无截断、机器可读的 sacct 查询；
- 明确成功/失败/活动/未知状态分类；
- 每个目录最终 attempt 必须 COMPLETED；
- 最终失败阻断依赖阶段并返回非零；
- 自动重投次数有界；
- job ID 和状态增量持久化；
- 中断恢复与重复提交保护；
- 真实状态 fixtures 和端到端测试。

## 验收条件

- `stage: all` 在任何文件或提交修改前报错；
- `submit: true --wait` 在任何 stage 都报错；
- `submit: false` 的 stage0/stage1 手动工作流不受影响；
- fire-and-forget 文档不暗示最终作业成功；
- README、workflow 和 CLI help 同步标记限制；
- 测试证明 safety gate 在 runner 创建和 `sbatch` 前触发。
