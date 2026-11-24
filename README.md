# 河湾气象站阵风采集与交班

值守班从单台模拟记录器接收阵风原行。白班接报并实收，值班员原位清卷，采集员在暂停或重开后补齐，夜班按成果号领取固定班报，逐条核对峰值的原始报送。

## 运行

需要 Python 3.11 标准库。在项目根目录运行 `python3 -B check_project.py` 执行完整回归。控制台物化仓库时提供此入口。也可用 `python3 -B -m unittest discover -s tests -t .` 直接执行 `tests` 包。

`python3 -B demo.py` 在临时站目录运行 `samples/day.json`。`python3 -B demo.py --directory ./demo-station` 可保留演示现场，指定目录须为空或尚未创建。

演示按以下顺序完成一天工作。

1. 值班员打开空白站目录，建立活动卷和空账。
2. 记录员验证报送，拒收一条负值并接收两条 `7\n`。
3. 采集员把两个 7 分别固定在旧卷的 `[0,2)` 和 `[2,4)`。
4. 值班员准备旧卷备稿，工作文件继续接报。
5. 接收 `12\n` 后完成切界。12 仍归旧卷 `[4,7)`，原工作文件清空后继续接收 `7\n29\n29\n`。
6. 采集器尝试补收旧尾。演示让 SQLite 拒绝一次明细写入，核对原批和断点仍可领取。
7. 重开站目录，补收旧卷尾部，再实收新卷。重试后共六个样本，无待采行。
8. 夜班指定这三个实收批，签出样本数 6、峰值 29 m/s 和两条并列出处。
9. 夜班重开后按原号领取同一班报，回读新卷 `[2,5)` 与 `[5,8)`，均为 `29\n`。
10. 带号补传另起一个站：X 的 `07\n` 完整写成后回复丢失，重开、轮转并补投后实收回旧卷 `[2,5)`；Y 的 `07\n` 实收回新卷 `[2,5)`，两笔各一份原件。

带号部分由 `python3 -B submission_fixture.py verify|demo` 核验并展示，输出 X、Y 各自的实收批号、批内序位和卷内地址。

## Python API

公开入口是 `from station import Station`。`Station.initialize(directory, work_name=None)` 首次在空白目录建站，默认工作路径为 `live/gust.log`。可指定 `live` 下的单个文件名。重开沿用登记的文件名，显式指定别名或缺少有业务意义的持久文件时抛出 `StationStateError` 并保留现场。已有数据通过下述业务入口修改。工作文件、数据库和封闭原件由站点管理。一个运行进程管理同一站目录，进程内可由多个线程或多个 `Station` 实例操作。停止后可用原目录重新打开。

| 接口 | 结果和错误 |
| --- | --- |
| `append(raw: bytes)` | 校验并原样追加一条记录，返回 `ByteSpan(volume, start, end)`。坏行抛出 `InvalidGustLine` 且不占字节位置。 |
| `submit(report_key, raw)` | 按报送号接报并返回状态字典。同号同原字节重投沿用原办件，异字节抛出 `ValueError` 且原办件不变，坏行抛出 `InvalidGustLine`。 |
| `submission(report_key)` | 按报送号查询状态字典，缺号为 `unknown`。 |
| `rotate()` | 预拷贝当时的前缀，再在正式切界时补齐旧卷，返回 `CutResult(sealed, active)`。备稿失败抛出 `PrecopyFailed`，原卷继续接报。并行轮转使备稿失效时，过时的轮转失败，调用者可重试。 |
| `read(volume, start, end)` | 返回半开区间内的原始 `bytes`。合法空区间返回 `b''`，未知卷抛出 `UnknownVolume`，非法区间抛出 `InvalidByteRange`。 |
| `poll(chunk_size=4096)` | 从正式断点读取，返回持久 `ReceiptBatch` 或在无进展时返回 `None`。提交失败或另一采集先提交同一起点时抛出 `PollCommitFailed`，调用者可重试。 |
| `checkpoint()` / `receipt(batch_id)` | 领取正式续采位置和已提交实收批。未知批号抛出 `KeyError`。 |
| `issue(parent_ids)` | 按非空的有序父批签出 `StoredReport`。缺失、重复或空清单抛出 `InvalidParents`。 |
| `get(report_id)` / `peak_evidence(report_id)` | 按原号领取固定班报，或按峰值出处顺序领取带地址的原字节。未知成果号抛出 `UnknownReport`。 |

卷号从 1 开始，批号和成果号由各自的持久账分配。记录格式为一个或多个 ASCII 数字加一个 LF，允许零和前导零。阵风单位是整数 m/s，解析、存储和峰值比较保持精确。行区间包含 LF，等值实测按不同源地址分别实收。分块末尾未解出的片段暂存于读取尝试，断点只随已提交完整行或真实过卷前进。一次实收批最多含一个卷的行，旧卷待采行先成批，旧卷耗尽后才读后继卷。只过卷且没有完整新行时会产生可领取的空批。班报按请求父批及批内顺序计数并保留所有并列原据。只有空批时样本数是零，峰值是 `None`，出处为空。

带号接报的详细口径见 `SUBMISSIONS.md`。报送号与实收行同库持久，`poll()` 与 `receipt()` 返回的行带 `report_key`，普通行该属性为 `None`。完整原行首次写成的卷和字节区间在重开、轮转和重投后保持不变；未重投的前缀残片在重开时原位补齐，不能作为原行前缀的残片被撤去并允许同号重投。

`rotate` 的 `after_prepare` 和 `after_prefix_block` 可观察备稿进度，`poll` 的 `after_chunk` 可观察读块。这些回调在工作文件门控之外执行。回归用显式事件安排暂停点，用实际 SQLite 写入拒绝检查回滚。当前范围为单站，轮转进程崩溃恢复和自动归档清理不在本版范围内。
