# P2-4：`outcar_patterns` 未在配置加载时验证

- 状态：讨论已完成
- 评审日期：2026-07-11
- 结论日期：2026-07-11
- 优先级：P2
- 关联位置：
  - `src/dpmoire_lite/config.py:15-20`
  - `src/dpmoire_lite/config.py:262-314`
  - `src/dpmoire_lite/outcar.py:10-22`
  - `src/dpmoire_lite/collect.py:100-121`

## 功能目的

结构弛豫可能在达到收敛前因 walltime 或其他集群限制结束。为了保留已完成
ionic steps 的训练信息，用户可能在续算前执行：

```text
cp OUTCAR OUTCAR0
```

也可能采用 `OUT1`、`out2` 或项目自定义的后缀。`outcar_patterns` 的目的
是让 collect rlx 找到同一弛豫目录中的所有历史 OUTCAR 分段，并把其中可用
数据一起收集，而不是只读取当前 `OUTCAR`。

要求用户在 YAML 中明确填写正则列表是合理的，因为文件命名约定属于用户的
集群工作流，DPmoire-lite 无法可靠猜测所有后缀。

## 最终问题表述

当前 loader 对配置值直接执行：

```python
tuple(outcar_patterns)
```

它没有验证输入是字符串列表，也没有预编译正则。

### 标量字符串

以下错误配置：

```yaml
outcar_patterns: '^OUTCAR$'
```

会被转换成字符 tuple：

```python
('^', 'O', 'U', 'T', 'C', 'A', 'R', '$')
```

其中 `^` 可匹配几乎所有文件名，使 INCAR、POSCAR、日志等被错误当成
OUTCAR 尝试解析。

### 非法正则

以下配置能通过 config load：

```yaml
outcar_patterns:
  - '^OUTCAR$'
  - '['
```

直到 collect 调用 `re.compile('[')` 才抛出 `re.error`。该异常发生在单来源
容错逻辑之外，会中断整个收集。

### 其他无效类型

空列表、空字符串、数字或字符串与其他类型混合，也会产生静默零匹配或延迟
到运行期的类型错误。

## 已确认的配置规则

### YAML 形态

自定义 `outcar_patterns` 必须是非空 YAML list：

```yaml
outcar_patterns:
  - '^OUTCAR\d+$'
  - '^OUT\d+$'
  - '^out\d+$'
  - '^OUTCAR$'
```

- 不接受标量字符串；
- 不接受空列表；
- 每个元素必须是非空字符串；
- 不接受数字、null、mapping 或混合类型；
- 未提供字段时继续使用项目默认列表。

错误信息应展示正确 YAML 示例，避免用户把单个正则误写成标量。

### config load 时编译

`load_config()` 必须逐项执行正则编译验证。非法表达式立即抛出
`ConfigError`，错误包含：

- 字段名；
- list index；
- 原始 pattern；
- Python regex 错误原因。

示例：

```text
Invalid outcar_patterns[1] '[': unterminated character set
```

collect 开始前就能发现错误，不得在遍历到某个计算目录后才失败。

### 重复 pattern

完全相同的 pattern 不会使一个文件被收集两次，因为文件发现对每个目录项
使用 `any(pattern.match(...))`。为保持配置清晰，程序可以按首次出现顺序
去除完全相同的 pattern 并发出警告；不尝试判断两个不同正则是否语义等价。

## OUTCAR 系列发现规则

- 每个文件只要匹配列表中任意一个 pattern 就被选中；
- 同一文件匹配多个 pattern 也只收集一次；
- pattern 使用当前 `re.match` 语义，推荐用户用 `^...$` 明确完整文件名；
- pattern 在 YAML list 中的顺序同时定义分段 family 的优先级；
- 文件匹配多个 pattern 时归入第一个匹配的 pattern；
- 同一 pattern 内按文件名 natural sort，数字按数值比较，例如 `OUTCAR2`
  位于 `OUTCAR10` 前；
- 不再使用 mtime 排序，因为跨集群复制、打包和恢复都会改变 mtime；
- 默认 pattern 顺序把数字历史分段放在无后缀当前 `OUTCAR` 之前；
- 所有最终选中的文件路径、pattern index 和确定性处理顺序写入 collect
  manifest；
- 缺少任何匹配时按来源 skipped 记录，不崩溃；
- 单个匹配文件尾部截断时，遵循 P2-1 的受控尾部抢救规则；
- 不对不同 OUTCAR 分段执行模糊结构去重，用户选择匹配范围即表示希望收集
  这些分段中的完整有效帧。

默认 pattern 继续覆盖以下名称，并使用下列顺序：

```yaml
outcar_patterns:
  - '^OUTCAR\d+$'
  - '^OUT\d+$'
  - '^out\d+$'
  - '^OUTCAR$'
```

用户采用 `OUTCAR_1`、`OUTCAR.backup` 等其他命名时，应在 YAML list 中
显式增加对应正则。

## 与 MD ML_ABN restart 的区别

OUTCAR 续算通常产生多个独立历史文件，因此通过 pattern 收集多个分段。

MD MLFF restart 通常执行 `cp ML_ABN ML_AB`，最终 ML_ABN 会在更新后的
database 基础上继续增长。MD collect 不应仿照 OUTCAR pattern 收集多个
ML_ABN 并重复合并，而应读取最终 ML_ABN，并始终只跳过 Stage1 initial
init_mlff seed 数量。该规则记录在 P2-1 note。

## 后续实现的验收条件

以下仅记录未来实现要求，本 note 不包含代码修改：

- 未配置 `outcar_patterns` 时使用默认列表；
- 标量字符串被拒绝并显示 YAML list 示例；
- 空列表、空字符串和混合类型在 config load 时被拒绝；
- 非法正则在 config load 时给出 index 和表达式；
- 合法自定义后缀能够选中全部预期 OUTCAR 分段；
- 一个文件匹配多个 pattern 时只出现一次；
- pattern list 顺序和 natural filename sort 产生跨机器一致的顺序；
- 修改 mtime 不改变处理顺序；
- `OUTCAR2` 排在 `OUTCAR10` 前，无后缀 `OUTCAR` 按默认规则最后处理；
- 完全重复 pattern 可去重并警告；
- 所选文件列表和顺序写入 manifest；
- 单个分段失败或部分截断不会隐藏其他分段的收集结果；
- 测试覆盖默认列表、自定义后缀、标量、空列表、混合类型、非法正则、重复
  pattern 和多 pattern 命中同一文件。
