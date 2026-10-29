# 领域词典 v1

| 对象 | 归属与粒度 | 版本与边界 |
| --- | --- | --- |
| Property | 租户内的物理酒店物业，内部 UUID | 名称不唯一；标准酒店版本追加保存 |
| Supplier entity | 某租户供应商下的外部酒店/房型/计划 | 外部编码为字符串，父级范围参与自然键；G1 实现 |
| Room type | 属于一家标准酒店 | King/Twin/人数等关键事实独立于名称，变更生成版本 |
| Rate plan | 属于标准房型 | 餐食、取消、支付、税费构成政策版本 |
| Inventory pool | 某供应商房型的库存来源池 | 日库存按池、日期、激活版本唯一；G1 实现 |
| Rate cell | 来源计划、日、人数、币种、激活版本 | 金额为单晚净价，不能自动当作含税订单总价 |
| Offer | 标准酒店/房型/计划、渠道与来源选择的组合 | 报价不代表订单或锁房；G1 实现 |
| Snapshot | 确定来源、映射、规则、政策与渠道能力版本的编译结果 | 审批绑定不可变 payload_hash；G1 实现 |
| Listing | 渠道账号下的商品 | 内部 UUID 与远端编码独立；后续跟踪 desired/applied/observed |

processing run 是同一来源的处理尝试，不等于来源激活版本。mapping version 是映射决策版本，不等于主数据内容版本。source_sequence 只在供应商及覆盖分区范围内比较，不跨供应商比较。

未知使用显式 UNKNOWN 或 nullable 的契约定义；未知库存与零库存分离。G0 完整 CSV fixture 不允许空 quantity，业务纯函数可以用 None 表示尚未知晓的库存，二者并不冲突。
