# P2-6：collect 结果状态与 CLI 退出码矩阵

- 状态：讨论已完成
- 决定日期：2026-07-11
- 优先级：P2
- 关联位置：
  - `src/dpmoire_lite/collect.py`
  - `src/dpmoire_lite/cli.py`
  - P2-1、P2-3、P2-4、P2-5

## 问题

collect 允许 skipped、partial、failed 和 no_data，但当前 CLI 没有稳定契约
区分完整成功、可用但降级、没有新数据和致命失败。脚本无法仅凭退出码判断
是否可以继续使用输出。

## 已确认的退出码

| Exit code | 状态 | 契约 |
| ---: | --- | --- |
| 0 | `complete` | 新候选包含至少一帧，全部预期来源完整成功，输出和 manifest transaction 已提交 |
| 1 | `fatal` | 配置/provenance/manifest 不变量失败，pending transaction 无法恢复，或写出/备份/发布失败 |
| 2 | `degraded` | 至少一帧已安全发布，但存在 partial、skipped、failed 或来源覆盖下降 |
| 3 | `no_data` | 本次接受 0 帧，没有发布新输出；旧输出按 P2-3 保留或原本不存在 |

所有非零退出码都可被 shell/调度脚本识别为“不是完整成功”，同时 2 和 3
允许调用方区分可用降级输出与无新数据。

## 来源状态映射

### complete

- `frames > 0`；
- 所有 manifest 预期来源均为 complete；
- 无 skipped、partial、failed；
- candidate、backup、final、manifest 和 journal 全部按 P2-3 提交；
- exit 0。

### degraded

- `frames > 0` 且输出已安全发布；
- 任一来源为 partial、skipped 或 failed，或相比上一事务来源覆盖下降；
- manifest 精确记录未贡献/部分贡献来源；
- exit 2。

单个 seed-prefix digest mismatch 可以使该来源 failed；如果其他来源仍产生
有效帧，整体为 degraded。错误来源贡献 0 帧。

### no_data

- 本次 `frames == 0`；
- 不发布空 extxyz；
- 旧输出存在时保持字节不变；
- manifest 记录来源失败/跳过原因和 `preserved_previous_output`；
- 无论旧输出是否存在，exit 3。

### fatal

以下情况不降级为普通来源失败：

- stage manifest 完全缺失；
- manifest schema 或目录越界；
- config 无效；
- collect pending journal 状态/hash 无法恢复；
- candidate 写出或回读验证失败；
- backup 创建/hash 验证失败；
- final `os.replace` 或 manifest transaction 提交失败；
- 无法保证正式输出与 manifest 的一致性。

这些情况 exit 1。若 P2-3 journal 能确定性完成一个已开始的事务，应先恢复；
恢复完成后按恢复事务的 complete/degraded 状态返回，否则 exit 1。

## API 契约

- collect 核心返回结构化结果 enum/dataclass，不通过解析日志决定退出码；
- CLI 是唯一把状态映射为整数退出码的边界；
- 当现有 manifest 可安全读取且事务允许原子写入时，manifest
  `collect.status` 与 CLI 状态名称一致；
- stderr 输出简洁摘要，manifest 保留完整 per-source 诊断；
- `no_data` 不通过抛出异常实现，以便先原子记录 manifest；
- fatal config/provenance 错误若尚未开始 transaction，可以直接报错退出 1；
- config 无效、stage manifest 完全缺失/不可读等 fatal 发生在安全 manifest
  写入边界之前，不能为了记录状态而创建或覆盖 manifest；此时退出码 1 和
  stderr 是权威结果。

## 验收条件

- 全部来源完整且有帧返回 0；
- 尾部 partial 且有帧发布返回 2；
- 某些来源 failed、其他来源成功返回 2；
- 全部来源 skipped/failed、0 帧返回 3；
- 0 帧且旧输出保留返回 3；
- manifest 缺失、publication 或 journal 恢复失败返回 1；
- 仅当 manifest 可安全写入时，退出码与 `collect.status` 一致；
- config 无效或 manifest 缺失的 fatal 返回 1，且不创建伪造 manifest；
- CLI 测试直接断言整数，不只断言日志文本。
