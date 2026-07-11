# Low-reasoning Model Execution Prompt

This prompt is designed to be pasted repeatedly into a lower-reasoning model.
It resumes from the local status file, executes at most one green checkpoint unit,
and leaves enough evidence for a later quota window.

## Copyable Prompt

````text
你正在维护 DPmoire-lite。严格执行已经批准的 implementation roadmap，不要重新设计方案。

工作区：
E:\codespace\MLFF\DPmoire-lite\.worktrees\code-review-followup

总路线图：
docs/superpowers/plans/2026-07-11-code-review-followup/README.md

拆分设计：
docs/superpowers/specs/2026-07-11-code-review-followup-decomposition-design.md

MLFF full-dedup/旧目录兼容设计：
docs/superpowers/specs/2026-07-12-mlff-full-dedup-legacy-collection-design.md

恢复状态文件：
docs/superpowers/plans/2026-07-11-code-review-followup/IMPLEMENTATION-STATUS.local.md

当前已知恢复基线：
- branch: codex/code-review-followup
- Plan 00 和 Plan 01 已完成
- Plan 02 已在 2026-07-12 修订为 4 个自包含 Task
- full-dedup 设计 checkpoint 为 54e9886，authoritative P2-1/P2-6 amendment 为 d69a15b
- 路线图包含独立 Plan 08A，避免扩张默认 Plan 08
- 实际 HEAD 可能包含后续计划文档修订，必须以 git 为准
- 当前可能存在未提交的 atomic I/O 文件和修订前 Manifest v2 RED 测试

本提示词授权你：
- 在当前 active execution unit 范围内修改测试、生产代码和对应文档；
- 在 checkpoint 全绿后创建本地 commit；
- 更新本地状态文件。

本提示词不授权你：
- push、PR、merge、rebase；
- 安装或升级 Python/conda 包；
- 删除、移动或提交 example-test 原始数据；
- 提交 POTCAR；
- 修改 active unit 以外的生产行为；
- reset、checkout 或覆盖用户改动。

## 一、执行单位，不是机械的一次一个编号 Task

每次模型运行最多完成一个 execution unit，然后停止。

execution unit 定义：

1. 默认是一个同时包含 RED 和 GREEN 的自包含 Task；或
2. 如果计划明确把“只添加 failing tests”和“实现这些 exact tests”拆成相邻两个 Task，则这两个相邻 Task 合并为一个 execution unit、一个 checkpoint。

允许的配对示例：
- Task N 只添加 RED tests；
- 紧接着的 Task N+1 只实现这些 exact tests；
- 两者之间没有其他无关 Task。

禁止：
- 为了让旧 RED 变绿而跳过一个无关 Task；
- 把非相邻 Task 合并；
- 一次执行两个独立 GREEN checkpoint；
- 当前 Task 添加属于未来非相邻 Task 的测试；
- 在默认测试发现中跨 checkpoint 留下 future-task RED tests。

如果计划顺序违反这些规则，先记录 blocker，不要自行跨 Task 实现。

## 二、启动与恢复协议

每次运行开始时：

1. 完整读取仓库中的 AGENTS.md。
2. 读取总路线图 README。
3. 完整读取 IMPLEMENTATION-STATUS.local.md。
4. 只读取 active plan 和它直接链接的 authoritative specs。
5. 运行：
   - git status --short --branch
   - git log -5 --oneline --decorate
   - git diff
   - git diff --cached --name-status
6. 核对状态文件中的 HEAD、owned changes、tests 和 Exact next action。

仓库事实优先于状态文件。

若不一致：
- 不要 reset、checkout、clean 或删除用户文件；
- 检查实际 diff、未跟踪文件和最近 commit；
- 修正状态文件；
- 从实际可证明的 phase 恢复。

不要对同一个未变化 blocker 连续重复审计。确认一次、更新 checkpoint、停止并请求明确修订。

## 三、Plan 02 一次性迁移规则

如果状态仍处于 Plan 02 atomic I/O checkpoint，并同时存在：

- src/dpmoire_lite/atomic_io.py
- tests/test_atomic_io.py
- tests/test_manifest_v2.py

则按修订后的 Plan 02 处理：

1. atomic_io.py 和 test_atomic_io.py 属于 amended Plan 02 Task 1。
2. test_manifest_v2.py 是修订前未提交的 future-task RED inventory，本 Task 禁止暂存。
3. 先运行 atomic focused suite。
4. committed-scope regression 使用：
   & $python -m pytest --ignore=tests/test_manifest_v2.py -q -p no:cacheprovider
5. 只有该回归全绿，才显式暂存以下两个文件：
   - src/dpmoire_lite/atomic_io.py
   - tests/test_atomic_io.py
6. 检查 cached diff，确认 test_manifest_v2.py、状态文件和 pytest 临时目录未暂存。
7. 创建 atomic-only checkpoint commit。
8. 更新状态为 amended Plan 02 Task 2。
9. Task 2 开始前，将 test_manifest_v2.py 限定为 Task 2 的五个 strict reader/schema tests；两个 path tests 到 Task 3 才加入。

这是唯一预先批准的 future RED ignore 例外。后续 Task 不得复制此例外。

## 三-A、冻结的 MLFF 双模式边界

只有 active plan 是 Plan 03、08、08A、09 或 10 时，才读取 full-dedup 设计；
只有 active execution unit 明确拥有相关行为时才修改代码。

冻结规则：

1. `seed-aware` 是默认模式，保持正常 workflow 的 Manifest v2 initial seed
   prefix 验证和跳过语义。
2. `full-dedup` 只能显式选择；全量读取每个选中目录的最终 ML_ABN，并用
   `mlab-config-v1` 删除 canonical 科学内容完全相同的帧。
3. exact dedup 单遍 hash，禁止 RMSD、容差、近邻、对称性和帧间两两比较。
4. 不从 `ML_ISTART`、`ML_MODE`、OUTCAR 或当前 `md/ML_AB` 推断 collection
   mode/initial seed。
5. 不收集 ML_AB、ML_ABN0、ML_ABN1 或 backup 中间文件。
6. missing manifest 只在 explicit full-dedup 中允许 bounded direct-child
   scan；invalid/unsupported/escaping manifest 永远 fatal，不得 fallback。
7. legacy/missing 兼容结果写 `work_dir/MD_data.collect.yaml`，不得覆盖旧
   stage manifest；安全发布仍由 Plan 09/10 负责。
8. Plan 03 只提供 parser 和 identity；Plan 08 只提供默认 source/seed-aware
   candidate；Plan 08A 才实现 full-dedup/inventory；Plan 10 才接 CLI。

## 四、Python 与依赖

禁止使用 bare python、python3 或 WindowsApps Python。

固定解释器：
C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe

依赖 Python 前验证：
& 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe' -c "import sys; print(sys.executable)"

设置：
$python = 'C:\Users\Nice_Try\anaconda3\envs\vdwID\python.exe'
$env:PIP_NO_CACHE_DIR = '1'

标准完整测试：
& $python -m pytest -q -p no:cacheprovider

缺包时：
- 记录解释器、包名和原始错误；
- 不安装；
- 更新 checkpoint 为 blocked；
- 请求用户选择安装或切换环境。

## 五、TDD 规则

### RED

1. 只添加当前 execution unit 明确拥有的测试和最小 fixture。
2. 运行计划指定的 focused command。
3. 确认失败原因是目标行为尚未实现。
4. import、拼写、路径或 fixture 错误不算有效 RED。
5. 把命令、退出码和核心失败写入状态文件。

### GREEN

1. 只实现让本 execution unit RED 测试通过的最小行为。
2. 文件编辑必须使用 apply_patch。
3. 搜索优先使用 rg / rg --files。
4. 不做计划外重构。
5. 运行 focused suite。
6. 运行 affected suite。
7. checkpoint commit 前运行完整 suite。
8. 所有将被提交的测试必须通过。

如果完整 suite 失败：
- 列出所有失败；
- 区分当前实现回归、既有基线失败和未提交 future RED；
- 除 Plan 02 一次性迁移外，不允许 --ignore、skip 或 xfail 绕过；
- 不能标记 checkpoint complete。

### COMMIT

提交前必须：

1. git diff --check
2. git status --short
3. 显式 stage 当前 execution unit 拥有的具体文件
4. git diff --cached --name-status
5. git diff --cached --check
6. 确认状态文件、pytest 临时目录、example-test、POTCAR 和无关用户改动未暂存

禁止：
- git add .
- git add -A
- 目录级宽泛暂存
- git reset --hard
- git checkout --
- git clean
- 自动 push

commit 后：
- 记录新 SHA；
- 更新状态文件；
- 不开始下一个 execution unit；
- 返回用户。

## 六、Checkpoint 规则

以下时刻必须使用 apply_patch 更新状态文件：

1. 本次运行开始并完成仓库核对后；
2. RED 被确认后；
3. focused GREEN 后；
4. affected/full suite 后；
5. checkpoint commit 后；
6. 遇到 blocker 后；
7. 额度或上下文可能不足时；
8. 本次运行结束前。

状态文件永远不要 stage 或 commit。

如果额度可能不足：
- 不开始新的 execution unit；
- 优先停在 inspect、RED、GREEN、verify 或 commit 明确边界；
- 不跳过测试；
- 更新 Exact next action 后立即停止。

## 七、状态文件格式

状态文件必须保持以下结构：

# DPmoire-lite Implementation Status

- Updated:
- Branch:
- HEAD:
- Overall status: not_started | in_progress | blocked | complete
- Active plan:
- Active execution unit:
- Active task(s):
- Phase: inspect | red | green | verify | commit | blocked
- Last completed plan:
- Last completed execution unit:
- Last checkpoint commit:

## Pre-existing user changes

- 文件、状态、来源
- 是否属于当前 unit
- 处理原则

## Current owned changes

- 每个 owned 文件
- staged / unstaged / untracked
- 所属 Task
- 修改目的

## Test evidence

### RED

- Command:
- Exit code:
- Expected failure:
- Observed failure:

### GREEN

- Focused command:
- Result:
- Affected-suite command:
- Result:
- Full-suite command:
- Result:

没有实际运行必须写 not run，禁止推测 passed。

## Decisions already fixed

- 只记录 active plan/spec 或已批准修订中的规则。

## Blockers

- 无则写 none。
- 有则记录原始错误、已完成的一次核对和需要用户决定的事项。

## Exact next action

- 只能有一个可直接执行的下一步。

## Resume commands

- 只列恢复时第一批只读检查或当前 focused test。

## 八、测试材料规则

当前忽略的 `example-test/0-walltime_restart` 中已有 walltime/restart 候选材料。进入 Plan 03、07、08 或 08A 前，如果下列文件存在，必须先完整读取：

`example-test/0-walltime_restart/README.md`

已确认的材料边界：
- 该部分下载目录约 544 MB，包含 POTCAR，只能作为本地证据；
- 多个 MD OUTCAR 是自然 walltime 结束：有数百个完整 force/free-energy blocks、无正常 timing footer，并结束在后续 electronic output 中；
- `md/0_0/OUTCAR0` 记录 fresh on-the-fly start，后续 `OUTCAR` 记录 restart；`md/0_1/OUTCAR` 记录 fresh start；
- 用户已确认这些 MD 没有使用所提供的 `init_mlff/ML_ABN`/`ML_FFN` 作为 starting database；
- 当前 `md/0_0/ML_AB` 比 final ML_ABN 小，但其 exact copy-time provenance 未证明，也不需要作为测试前提；
- 新 walltime 样本是 VASP 6.5.1，另有 ignored VASP 6.4.1 ML_ABN/OUTCAR 证据；
- 当前提供的所有 ML_AB/ML_ABN 都以完整 stress block 结束，不是天然尾部截断 ML_ABN。

Plan 03/08 的 position、force、stress 尾部截断 ML_ABN fixture 必须从完整文件做确定性裁剪，并明确标为“constructed crop”。Plan 03 必须裁剪最小 VASP 6.4.1/6.5.1 portable format fixtures。Plan 07/08 可以从自然 walltime OUTCAR 裁剪最小 fixture，但必须由实际 parser 行为确认 frame/error 边界，不能只凭 block 数或缺少 footer 判定 partial。Plan 08A 的 fresh-start/restart 回归必须使用 portable synthetic/cropped data，不能直接依赖 raw tree。

要求：
- 原始 example-test 文件永远不提交；
- fixture 必须删除用户路径、账号、hostname、集群信息和无关输出；
- fixture 不得包含 POTCAR 或势函数内容；
- tests/data README 必须记录来源、裁剪目的和再分发确认；
- 正向 seed-prefix 测试使用 synthetic Manifest v2 identity 或另一个 provenance 已证明的 seed pair；
- 该样本是 confirmed zero-external-seed case，不得把 `init_mlff/ML_ABN` 当作 `md/0_0` 的 original seed；
- 不需要为了证明 full-dedup 规则下载更多大文件或证明当前 ML_AB 的 exact copy-time identity；
- 只有现有材料无法表达 authoritative spec 的格式边界时才阻塞请求用户材料；
- 请求时必须明确说明所需文件类型、完整/截断状态和需要保留的区块。

自然 walltime OUTCAR、fresh-start/restart 和 VASP 6.4.1/6.5.1 格式已有增强证据。自然尾部截断 ML_ABN 仍属可选增强证据，不是当前 Plan 02 或可由受控 fixture 覆盖之 parser/full-dedup 工作的前置条件。

## 九、范围控制

- 只执行 active execution unit。
- 不提前实现未来 Plan。
- 不重新讨论冻结的科学决策。
- 不浏览互联网重复验证已冻结的 VASP/ASE 规则。
- 不修改 deferred Slurm automation 或 MD restart。
- 不使用 private example-test 作为直接自动测试依赖。
- 不提交 POTCAR。
- 不解除 P1-2 safety gate。
- 不声称 Plan 完成，除非该 Plan checkpoint 全绿。
- 不声称 follow-up 完成，除非 Plan 00–10（包括 Plan 08A）和 final integration gate 全部通过。

## 十、本次运行结束输出

只报告：

1. 当前 Plan / execution unit / phase；
2. 本次实际完成内容；
3. 实际运行的测试及结果；
4. checkpoint commit；
5. 未提交 owned changes；
6. blocker；
7. Exact next action。

现在开始：

- 先执行启动与恢复协议；
- 以实际状态文件和 git 事实确定 active unit；
- 本次最多完成一个 execution unit；
- checkpoint 后停止，不继续下一个 unit。
````
