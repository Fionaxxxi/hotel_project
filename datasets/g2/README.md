# G2 合成样例

全部虚构，延续datasets既有许可/来源说明；不是AI评估集。`python -s -m infra.generate_g2`重建文件及sha256清单；XLSX容器时间戳可改变字节hash，规范化业务事实不变。

| 目录 | 顺序与期望 |
| --- | --- |
| equivalent/csv,xlsx,json | 同DEMO-G2物业，seq1→2→3；14条记录，generation事实与业务payload hash相同；各格式定位保留 |
| delta | 基线后seq4，仅FLEX 2027-01-11净价115.00，售价126.50，其他事实继承 |
| delta-delete | 同基线seq5，只删除FLEX 2027-01-12，自然键tombstone；不具完整日历时编译阻断 |
| quality | 独立物业，2027-01-11库存-1；该日阻断，选其他日必须重新确认，原manifest不变 |
| snapshot/base → authority-missing | seq1→2；显式authority内缺一价格行只关闭该自然键，留下tombstone |
| ordering/high → old / same-sequence | seq5基线后seq4/同5异输入冲突；并发CAS和不相交分区用tests/test_g2_faults.py独立生成 |
| recovery | 1005个唯一房型，合计1017条解析记录；用于检查点，不是可发布的业务闭环，引用不完整会在激活阻断 |

规模集不提交十万行静态文件：`infra/g2_benchmark_case.py`在隔离库生成唯一自然键的10000/100000房型，记录文件字节和实际提交数。崩溃/重复事件/租约场景由测试产生独立随机资源，避免手工复用演示序号污染结论。
