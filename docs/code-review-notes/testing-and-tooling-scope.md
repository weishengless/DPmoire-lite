# 测试与工具链范围

- 状态：讨论已完成
- 决定日期：2026-07-11
- 类型：跨问题质量保障

## 本轮实现必须包含

### 可移植解析 fixtures

- 从真实文件中裁剪小型、可再分发的 `ML_AB`、`ML_ABN` 和 OUTCAR；
- 放入仓库 `tests/data/`；
- 保留触发目标解析行为所需的最小内容；
- 不提交 `example-test` 中的大型完整计算输出作为单元测试数据；
- 核心 parser 和 collect 测试不再依赖开发者本地绝对路径：

  ```text
  E:\codespace\MLFF\03_constrained_shear_scan
  ```

- 完整、尾部截断和内部损坏场景分别使用明确 fixture。

### 已确认问题的回归测试

每个已确认 note 的验收条件必须映射到自动化测试，至少包括：

- P0：`example-test/` 被忽略且 POTCAR 不进入 git/wheel；
- P1-1：两条 `sc_rlx` 路径、`true=fixed` mask 及 source+image anchor identity；
- P1-2 safety gate：`stage: all` 和 submitted `--wait` 在任何副作用前失败；
- P1-4：INCAR 无空格、分号、注释、重复、缺失和 vdW kernel；
- P1-5：新 provenance、旧 manifest 双判据和扩胞冲突；
- P1-6：全零/非零速度、直接重写、扩胞和单层 MD；
- P2-1：完整、partial、内部损坏、单位/符号明确的 canonical seed digest、
  `declared=complete+1` 截断 header、NaN/Inf/负零和多次 MD restart；
- P2-2：已有 stage 拒绝原地 rebuild，Stage0 多目标先全量检查，删除后可重跑；
- P2-3：零帧保留、双 candidate 预写、原位旧输出、单写者锁、备份、确定性
  journal 状态表、committed-journal 残留故障注入和原子发布；
- P2-4：严格 YAML list、非法 regex、确定性顺序和多 OUTCAR 分段；
- P2-5：ASE 3.28/3.29 context-managed file-object iterator、严格解码、提前
  break 关闭句柄、首帧 lazy 边界与参考采样一致；
- P2-6：complete/fatal/degraded/no_data 的 CLI 整数退出码。

### 对称性约化

为 `find_sym_reduced_stackings()` 增加直接测试。示例配置默认启用
`symm_reduce`，该路径决定实际生成哪些 rlx 目录，不能只通过打包测试间接
覆盖。

测试应验证：

- 返回的 stacking 稳定且无重复；
- 写出的 stacking 列表与 manifest 一致；
- 缺少可选 pymatgen/spglib 依赖时给出明确错误或按项目定义降级；
- 矩形 `n_sectors` 行为正确。

### 打包验证

保留并扩展 wheel 测试，确认：

- 示例 config；
- 所有 INCAR 模板；
- 中英文 workflow 文档中必要的默认行为；
- 新增默认配置字段；
- init 命令复制出的示例内容

与源码版本一致。

### ASE 版本行为

本轮至少使用当前可用环境验证 ASE 3.28.x，并针对用户复现的 ASE 3.29.x
行为编写不依赖隐式版本细节的测试。测试验证语义，例如 `Atoms` 是否包含
`momenta`、输出是否具有速度区块，而不是匹配某种浮点文本格式。

## 暂缓到工具链或自动化开发

### CI matrix

暂不在本轮建设多操作系统、多 Python 和多 ASE 版本的完整 CI matrix。功能
测试应保持可在未来 matrix 中运行，但本轮不扩大到 CI 平台配置。

### lint 与静态类型检查

暂不新增全仓库 lint/type-check gate。可在修改代码时保持类型标注清晰，但
不把清理全部历史告警作为本轮范围。

### Slurm 集成测试

以下内容随 `deferred-slurm-automation.md` 暂缓：

- 真实 `sacct` 状态 fixture 全集；
- 提交、轮询、重试和恢复测试；
- 集群集成测试；
- job ID 持久化和进程恢复；
- 自动 MD restart 测试。

## 质量门槛

进入最终实现验收时应满足：

- 所有新增测试不依赖机器本地绝对数据路径；
- 没有因为缺少开发者私有 fixture 而跳过的核心解析行为；
- 每个本轮问题至少有一个先失败、后通过的回归测试；
- 完整测试套件通过；
- wheel 构建及 wheel 内容测试通过；
- 大型 `example-test` 仅作人工证据，不成为仓库测试负担。
- `git status` 不显示 `example-test/`，任何 POTCAR 均不进入测试 fixture。
