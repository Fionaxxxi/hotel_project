# 合成数据说明

全部数据为本项目手工设计的虚构数据，无真实酒店、个人资料或供应商商业条款。业务时钟固定为 `2027-01-01T02:00:00Z`，房态覆盖 2027-01-10～12；该时间仅是样例字段，API 令牌仍使用真实当前时钟。

使用项目解释器执行 `python -s -m infra.generate_assets` 可重建 manifest Schema、F00～F05 和稳定身份清单；生成器只覆盖这些受控资产，不删除数据库。种子入口 `python -s -m infra.manage seed` 可重复执行，不重置或删除已有库。

| 资产 | 意图 |
| --- | --- |
| F00-valid | 两个计划共享一份每日四间库存，三晚、USD、编码 001 |
| F01-invalid-inventory | 首日 quantity=-1，INVALID_VALUE |
| F02-missing-parent | Twin 房型引用 999，PARENT_NOT_FOUND，并可能同时报告范围错误 |
| F03-conflicting-key | 同池同日再出现八间，DUPLICATE_NATURAL_KEY |
| F04-invalid-scope | snapshot 缺日期上界，MANIFEST_SCHEMA |
| F05-hash-mismatch | 修改文件内容但不更新 manifest，HASH_MISMATCH |

这些是演示和契约回归样例，不是 AI 冻结评估集，不用于报告模型准确率。
