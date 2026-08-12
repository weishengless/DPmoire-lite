# P2-5：OUTCAR 在采样前被完整读入内存

- 状态：讨论已完成
- 评审日期：2026-07-11
- 结论日期：2026-07-11
- 优先级：P2
- 关联位置：
  - `src/dpmoire_lite/outcar.py:25-26`
  - `src/dpmoire_lite/dataset.py:113-119`
  - `src/dpmoire_lite/collect.py:100-121`

## 最终问题表述

当前 `read_outcar_frames()` 调用：

```python
read_vasp_out(str(path), ":")
```

ASE 会先把 OUTCAR 的全部 ionic steps 转换成内存列表。DPmoire-lite 随后才
在 `Dataset.load_outcar()` 中应用 `outcar_collect_freq`：

```python
for index, structure in enumerate(read_outcar_frames(path)):
    if index % freq == 0:
        self.add_atoms(structure)
```

如果一个 OUTCAR 有 10000 帧、采样频率为 20，最终只保留 500 帧，但峰值
内存仍需先容纳 10000 个 ASE `Atoms` 对象。大体系或长时间 MD OUTCAR 会
产生远高于最终数据集所需的内存占用。

## 问题来源

ASE `read_vasp_out(..., ":")` 是完整多帧读取接口。sampling 位于返回之后，
所以无法减少解析阶段的对象数量。

本机 `vdwID` 环境使用 ASE 3.28.0，提供：

```python
iread_vasp_out(filename, index=-1)
```

其官方 docstring 说明返回 generator，但仅检查返回对象类型不足以证明接口
可用或真正流式。

实际验证得到：

```python
g = iread_vasp_out(path_string, index=":")
type(g)  # generator
next(g)  # AttributeError: 'str' object has no attribute 'name'
```

ASE 3.28 的 OUTCAR chunk parser 需要具有 `.name` 的打开文件对象。传入文件
句柄后，首帧可正常产生并包含 energy、forces、free_energy 和 stress。

通用 `ase.io.iread(..., format="vasp-out")` 也不能作为本项目的流式保证：
在相关 ASE 路径中它可能经过 `read_vasp_out()` 的 `list(g)` 包装。实现不能
因为外层返回 generator 就假定底层没有预构造全部帧。

## 已确认的最小解决方案

### 文件句柄生命周期内逐帧读取

唯一受支持的实现入口是直接在文件句柄生命周期内迭代 VASP 专用 iterator：

```python
@contextmanager
def open_outcar_frames(path):
    with Path(path).open("r", encoding="utf-8", errors="strict") as fd:
        yield iread_vasp_out(fd, index=":")

with open_outcar_frames(path) as frames:
    for frame in frames:
        ...
```

要求：

- 不把 path string 直接传给 `iread_vasp_out()`；
- 不通过通用 `ase.io.iread(format="vasp-out")`；
- 对外暴露 context manager，而不是依赖普通 generator 的最终回收；
- iterator 的全部消费都发生在调用方 `with` 内，不能返回绑定到已关闭 fd
  的 iterator；
- 调用方 `break`、异常或正常结束都会先退出 `with` 并立即关闭文件；
- 禁止 `errors="ignore"`，因为它会静默吞掉损坏字节；
- 使用严格 UTF-8 解码（ASCII 是其子集），`UnicodeDecodeError` 进入来源损坏
  判定，不伪装成可抢救 EOF；
- 不在进入 Dataset 前构造完整帧列表；
- 每取得一个完整 `Atoms` 后立即决定是否采样；
- 只有命中 `outcar_collect_freq` 的帧才复制到总 Dataset。

### 保持现有采样语义

每个由 `outcar_patterns` 选中的 OUTCAR 分段独立从 index 0 开始采样：

```text
OUTCAR0: 0, freq, 2*freq, ...
OUTCAR1: 0, freq, 2*freq, ...
OUTCAR : 0, freq, 2*freq, ...
```

本轮不改为跨文件全局计数，避免同时改变数据选择和内存实现。所选分段按
P2-4 定义的 pattern priority + natural filename sort 确定，禁止依赖 mtime，
并将最终顺序记录到 collect manifest。

### 截断和损坏

- iterator 在 EOF 之前已经 yield 的完整帧可按 P2-1 尾部抢救规则保留；
- 最后一个未完成 ionic step 丢弃，来源标记 `partial`；
- 文件中间损坏不能静默跳过后继续；
- 解析错误记录源文件、已接受帧数和错误位置；
- 第一帧即失败时来源贡献 0 帧。

ASE iterator 是否能为所有截断形式提供足够错误位置，需要在实现时使用真实
截断 fixture 验证。如果无法区分尾部截断和内部损坏，应增加项目侧的边界
检测，不能把所有 parser exception 都当成可抢救尾部。

### 当前不实现磁盘流式聚合

本轮仍把被采样接受的帧保存在 Dataset 中，随后执行 P2-3 的：

- 临时 extxyz 写出；
- 回读验证；
- 旧输出备份；
- 原子发布。

这会把内存峰值从“全部原始 OUTCAR 帧”降低到“最终采样帧”。如果未来连
采样后的 Dataset 也无法容纳，再单独设计临时 extxyz 或分块磁盘聚合，不在
本轮引入额外事务复杂度。

## 与其他 note 的关系

- P2-1 定义完整、partial 和 failed 来源及尾部抢救；
- P2-3 定义汇总候选数据集的安全发布；
- P2-4 定义 OUTCAR 分段发现和正则预检；
- P2-5 只改变单个 OUTCAR 的读取内存行为，不改变选中文件和采样频率。

## 流式行为测试契约

测试不能只断言返回值是 generator，因为 ASE 3.28 的错误 path-string 入口
也满足这一断言。

ASE 3.28 和 3.29 都必须验证：

1. path string 直接传给底层入口的已知失败不会被包装层重新引入；
2. file object 入口能够读取首帧；
3. 多帧 OUTCAR 调用一次 `next()` 后，文件逻辑位置仍小于 EOF，证明首帧
   产生前没有扫描并构造全部帧；
4. 可使用记录 `read`/`readline` 和 `tell` 的受控 file wrapper，或等价的
   首帧读取边界断言；
5. 消费完整 iterator 后文件句柄被关闭；
6. 迭代中途异常或调用方 `break` 后，退出 context 时句柄立即关闭；
7. 非法 UTF-8 字节触发明确解码错误，不被忽略；
8. 流式结果与小型完整 fixture 的批量参考结果一致。

## 后续实现的验收条件

以下仅记录未来实现要求，本 note 不包含代码修改：

- OUTCAR wrapper 在打开文件句柄的上下文中 yield 帧；
- wrapper 是 context manager，调用方提前停止时也确定性关闭句柄；
- 严格解码，损坏字节不会被 `errors="ignore"` 吞掉；
- 不使用 path-string `iread_vasp_out()` 或通用 `ase.io.iread(vasp-out)`；
- 首帧产生前未把文件读取到 EOF，也未预构造全部帧；
- `freq=1` 收集全部完整帧；
- `freq=N` 对每个文件收集 index `0, N, 2N, ...`；
- 多个 OUTCAR 分段各自重置采样 index；
- 尾部截断保留此前完整且命中采样的帧并标记 partial；
- 内部损坏不被静默越过；
- 第一帧失败贡献 0 帧；
- 解析结果仍包含 energy、forces 和 stress；
- 与原批量读取在完整小型 fixture 上产生相同的采样帧；
- 测试使用真实或最小可再分发 OUTCAR fixture，不依赖开发者本地绝对路径；
- 对大量未采样帧的测试证明 iterator 不会同时保留全部原始 frame 对象。
