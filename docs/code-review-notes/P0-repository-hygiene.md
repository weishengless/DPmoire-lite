# P0：大型本地计算样例不得进入仓库

- 状态：实施前阻断项已确认
- 决定日期：2026-07-11
- 优先级：P0
- 本地目录：`example-test/`

## 问题

本地 `example-test/` 是用户用于复现实际 VASP 工作流的计算目录，不是可提交
测试 fixture。当前检查得到：

- 595 个文件；
- 约 49.69 MiB；
- 41 份 POTCAR；
- 包含 MLFF、OUTCAR、vasprun.xml 等大型或可能受许可约束的计算产物。

整个目录进入版本控制会造成仓库膨胀，并可能错误分发 POTCAR 等不应随项目
提交的内容。

## 已确认规则

- 仓库根目录 `example-test/` 以 `/example-test/` 加入 `.gitignore`，不误伤
  其他层级可能同名的测试目录；
- 不 stage、不 commit、不打包该目录中的任何原始大文件；
- 该目录只作为本地人工复现证据；
- 正式测试只能从中裁剪实现某个解析场景所需的最小、可再分发 fixture；
- fixture 必须移除 POTCAR、用户路径、作业账号、集群信息和无关输出；
- 每个 fixture 应具有来源说明、裁剪目的和许可/可再分发确认；
- fixture 放入 `tests/data/` 后单独检查大小和内容；
- 实现开始前 `git status` 不得再把 `example-test/` 显示为未跟踪内容。

## 本地目录处置

`.gitignore` 解决版本控制污染。物理目录可以保留在当前 worktree 供本轮人工
复现，也可以由用户移到仓库外；在未明确外部目标路径前，Codex 不擅自移动
或删除用户的本地计算数据。

## 验收条件

- `git check-ignore example-test` 成功；
- `git status --short` 不显示 `example-test/`；
- 仓库和 wheel 不包含任何 `example-test` 原始文件；
- `tests/data/` 中只存在小型、必要、可再分发 fixture；
- POTCAR 不进入 git history。
