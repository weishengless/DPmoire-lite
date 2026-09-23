# P1-4：INCAR 合法语法未被安全解析和改写

- 状态：讨论已完成；2026-09-23 修订 ENCUT 来源
- 评审日期：2026-07-11
- 结论日期：2026-07-11
- ENCUT 修订：2026-09-23。平面波截断改为配置中的 `encut`（单位 eV，省略时为 500），并写入每个生成的 INCAR。`encut_factor` 不再接受。本修订取代 `docs/superpowers/specs/2026-05-26-dpmoire-lite-design.md` 中的 `ENCUT = encut_factor * max(POTCAR ENMAX)`。
- 优先级：P1
- 关联位置：
  - `src/dpmoire_lite/inputs.py:72-82`
  - `src/dpmoire_lite/inputs.py:139-157`
  - `src/dpmoire_lite/build.py:390-409`
  - `tests/test_structures_inputs.py:67-82`

## 最终问题表述

当前 INCAR 处理使用按行和空白分词的方法。它不能安全处理 VASP 支持的
合法写法，例如：

```text
ENCUT=400
ML_RCUT1=6
ENCUT = 450; ISMEAR = -1
LUSE_VDW=T; ENCUT=400
```

已确认的结果包括：

- 无空格的 `ENCUT=400` 和 `ML_RCUT1=6` 不会被改写；
- 改写分号行中的第一个标签时，会删除同一行的其他语句；
- `LUSE_VDW=T; ...` 可能不被识别，导致缺少 `vdw_kernel.bindat`；
- 重复标签没有统一检测，可能把互相冲突的科学参数交给 VASP；
- 原实现会把 `LANGEVIN_GAMMA` 的用户值无条件替换为若干个 `1`，越过了
  DPmoire-lite 应有的职责边界。

这些行为可能静默保留错误 cutoff、删除用户标签、漏复制必要文件，或擅自
改变用户选择的物理参数。

## VASP 格式依据

VASP 官方 INCAR 格式允许：

- `tag = values` 中等号两侧任意留白；
- 使用分号 `;` 在同一行放置多个语句；
- 使用 `#` 或 `!` 开始注释；
- 使用反斜杠延续长行；
- 使用引号包含跨行或含特殊字符的值；
- 标签名称不依赖用户采用统一大小写。

参考：<https://vasp.at/wiki/INCAR>

官方文档没有为本项目提供足够依据来依赖重复标签的某个固定优先顺序。
DPmoire-lite 不应猜测“第一个”或“最后一个”会生效，而应在生成前消除歧义。

## 问题来源

### 改写器按空白识别标签

`replace_incar_values()` 使用：

```python
words = line.split()
key = words[0].upper()
```

因此 `ENCUT=400` 的第一个单词是 `ENCUT=400`，不会匹配 `ENCUT`。匹配到
目标标签时，函数又用一条全新文本替换整行，所以分号后的无关语句和原始
注释都会丢失。

### vdW 检测只关注行首标签

`needs_vdw_kernel()` 虽然对等号做了简单分词，但每行只判断第一个标签并
立即返回，不能可靠处理分号语句、重复定义和更复杂的注释位置。

### 职责边界过宽

当前改写器把 `LANGEVIN_GAMMA` 写成与元素类型数量相同的一组 `1`，无论
用户原来选择什么摩擦系数。该值不是由 DPmoire-lite 配置推导出来的，程序
不应替用户决定。

## 已确认的职责边界

DPmoire-lite 不是完整的 INCAR 物理参数检查器。用户对具体 VASP 设置负责，
程序只保证自己管理的参数和通用重复检查正确。

### 程序受控标签

只有以下三个标签由 DPmoire-lite 决定数值：

- `ENCUT`：由配置 `encut` 直接给出，单位 eV，省略时为 500；
- `ML_RCUT1`：由 `r_cut` 或项目的自动 cutoff 规则计算；
- `ML_RCUT2`：与上项相同。

### 用户负责的标签

其他标签全部保留用户值，包括但不限于：

- `ISMEAR`；
- `IBRION`；
- `MDALGO`；
- `LANGEVIN_GAMMA`；
- `LANGEVIN_GAMMA_L`；
- `TEBEG` 和 `TEEND`。

DPmoire-lite 不广播、不补全、不校验这些标签的物理合理性。现有
`LANGEVIN_GAMMA -> 1 1 ...` 的改写行为应删除。

`LUSE_VDW` 是操作依赖检查的例外：程序读取其最终无歧义的有效值以判断
是否复制 `vdw_kernel.bindat`，但不修改该标签。

## 已确认的重复标签规则

标签名称按不区分大小写比较。注释中的文本不构成有效定义。

### 受控标签重复

对于 `ENCUT`、`ML_RCUT1` 和 `ML_RCUT2`，无论源值是否相同：

1. 在生成的 INCAR 中注释所有原始重复定义；
2. 在最后一个重复定义附近写入唯一的程序计算值；
3. 保留被禁用的原始文本用于追查；
4. 按“模板 + 标签”发出一次警告；
5. 继续 build。

示例输入：

```text
ENCUT=400  # old value
PREC = Accurate
ENCUT=520  # intended for hard POTCAR
```

若程序计算值为 600，生成结果为：

```text
# DPmoire-lite disabled duplicate: ENCUT=400  # old value
PREC = Accurate
# DPmoire-lite disabled duplicate: ENCUT=520  # intended for hard POTCAR
ENCUT = 600  # DPmoire-lite generated value
```

### 非受控标签重复且值相同

如果重复值在忽略等号周围和首尾空白后完全相同：

1. 保留第一个有效定义；
2. 注释后续重复定义；
3. 发出警告；
4. 继续 build。

示例：

```text
ISMEAR=-1
ISMEAR = -1
```

程序只做保守的文本规范化，不猜测语义等价。`.TRUE.` 与 `T`、`0` 与
`0.0` 等不同文本不自动视为相同。

### 非受控标签重复且值不一致

如果值不一致，DPmoire-lite 不替用户选择：

1. 在创建计算目录或备份前终止；
2. 报告模板路径、标签、语句位置和全部冲突值；
3. 不生成部分计算目录；
4. 用户修改输入模板后重新运行 build。

示例：

```text
ISMEAR = -1
ISMEAR = 0
```

期望错误信息包含：

```text
input/MD_INCAR contains conflicting definitions:
  ISMEAR at line 12: -1
  ISMEAR at line 38: 0
No calculation directories were generated.
```

### 分号行中的重复

以下内容同样视为重复：

```text
ENCUT=400; ISMEAR=-1; ENCUT=520
```

处理重复语句时必须保留 `ISMEAR=-1`。如果需要把被禁用语句移到独立注释
行，可以规范化该行布局，但不得删除或改变无关标签。

## 已确认的缺失标签规则

### ENCUT

每个生成的 INCAR 都必须有唯一有效的 `ENCUT`。源模板缺失时，程序自动
追加计算值、发出警告，但不修改源模板：

```text
ENCUT = 600  # DPmoire-lite generated; missing from source template
```

这保证显式配置的 `encut` 不会静默失效；省略 `encut` 时使用 500 eV。

### ML_RCUT1 和 ML_RCUT2

- 已存在时，使用 DPmoire-lite 计算值改写；
- 如果模板有效启用了 `ML_LMLFF = T`、`.TRUE.`、`.T.` 或 `TRUE`，缺失的
  `ML_RCUT1/2` 自动补充并警告；
- 只缺一个时只补缺失项；
- 如果模板没有启用 MLFF，并且标签原本不存在，则不主动插入 MLFF 标签。

示例：

```text
ML_LMLFF = T
ML_RCUT1 = 6
```

计算 cutoff 为 7.2 时生成：

```text
ML_LMLFF = T
ML_RCUT1 = 7.2
ML_RCUT2 = 7.2  # DPmoire-lite generated; missing from source template
```

## 解析和输出要求

1. 同一套语法解析结果必须同时供标签改写、重复检查和
   `needs_vdw_kernel()` 使用，避免三个模块对同一模板得出不同结论。
2. 必须识别无空格赋值、大小写标签、分号语句、`#`/`!` 注释。
3. 必须尊重引号和续行，不能把引号内的 `;`、`#`、`!` 当成语法边界。
4. 非目标语句、原始注释和有效顺序应尽量保持。
5. 如果某段语法无法安全解析并且影响受控标签或重复判定，应带位置报错，
   不能静默猜测或破坏原文。
6. 源模板只读。所有自动注释和规范化仅出现在生成的 INCAR 中。

## 警告和错误呈现

- 可自动修复的问题按“源模板 + 标签”警告一次，避免同一模板用于大量目录
  时重复刷屏；
- 每个生成的 INCAR 保留自动修复注释，使目录复制到其他集群后仍能追查；
- 冲突错误应在阶段预检时聚合报告；
- 预检必须发生在备份已有目录和创建新目录之前；
- 错误信息应明确说明没有生成计算目录，用户修正模板后可重新运行。

## 后续实现的验收条件

以下仅记录未来实现要求，本 note 不包含代码修改：

- `ENCUT=400`、任意等号空格和小写标签都能正确改写；
- 分号行改写后所有无关语句仍存在且值不变；
- 注释中的伪标签不参与改写或重复检查；
- 受控标签重复时只有一个程序计算值保持有效；
- 非受控相同重复项可自动去重并产生警告；
- 非受控冲突重复项在任何目录变更前阻止 build；
- 缺失 `ENCUT` 自动补充；
- 启用 `ML_LMLFF` 时缺失的 `ML_RCUT1/2` 自动补充；
- 未启用 MLFF 时不凭空添加 `ML_RCUT1/2`；
- `LANGEVIN_GAMMA` 的用户值不再被替换为 `1`；
- `LUSE_VDW=T` 位于任意合法语句位置时都能触发 vdW kernel 依赖；
- 冲突的 `LUSE_VDW` 重复定义在复制文件前报错；
- 测试覆盖分号、注释、大小写、无空格、重复、缺失、续行和引号场景。
