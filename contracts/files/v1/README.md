# 文件契约 v1.0

本目录 manifest 使用 JSON Schema Draft 2020-12。CSV 逻辑记录校验由 `services/core/file_contract.py` 执行。G0 校验入口面向受控、完整测试包，不能当作生产上传安全网关。原始代码以字符串保留前导零，默认 UTF-8、英文逗号、标准 CSV 引号；无注释行；首行为表头。空必填字段拒绝；未知字段和重复表头拒绝。

## Manifest

必需字段：schema_version=1.0、supplier_code、正整数 source_sequence、带时区 generated_at、mode(snapshot/delta)、scope、files、contract_version。禁止额外字段，尤其不接受 tenant_id。scope 必须含物业代码集合、实体集合及 `[stay_date_from, stay_date_to_exclusive)`；文件清单名称使用固定白名单，SHA-256 为实际内容的 64 位小写十六进制摘要。

## 字段字典

| 文件 | 必需字段 | 类型/校验与自然键 |
| --- | --- | --- |
| properties.csv | property_code,name,country_code,city,address,timezone | 全部字符串；G0 合成集国家仅 CN；时区必须为可解析 IANA；自然键 property_code |
| rooms.csv | property_code,room_code,name,max_adults,bed_type | max_adults 为正整数字符串；bed_type=KING/TWIN/UNKNOWN；自然键 property_code+room_code；引用包内酒店 |
| rate_plans.csv | property_code,room_code,plan_code,meal,cancellation,payment,tax_basis | 自然键 property_code+room_code+plan_code；引用包内房型；cancellation 为 CSV 转义的 JSON 对象 |
| inventory.csv | property_code,room_code,pool_code,stay_date,quantity,stop_sell,observed_at | 自然键 property_code+room_code+pool_code+stay_date；quantity 非负整数；stop_sell 为 true/false；observed_at 带时区；stay_date 在 scope 内 |
| rates.csv | property_code,room_code,plan_code,pool_code,stay_date,adults,currency,net_amount | 自然键 property_code+room_code+plan_code+stay_date+adults+currency；adults=2,currency=USD；正 Decimal、最多四位小数且满足 NUMERIC(18,4)；关联计划及同日库存池 |

G0 演示枚举：meal=BREAKFAST_2/NONE/UNKNOWN；payment=AT_PROPERTY/PREPAID/UNKNOWN；tax_basis=NET_EXCLUSIVE/GROSS_INCLUSIVE/UNKNOWN。UNKNOWN 仅表示输入事实未知，绝不表示可售，后续编译必须阻断不完整条款。

取消政策：kind=FLEX/NONREF，deadline_hours 为非负整数，penalty_basis=stay_total。此为合成数据的最小结构，不等同于已支持任意阶梯取消。总设计中的原文和结构化阶梯扩展必须显式升级契约。

rates.csv 的 pool_code 是 G0 新增的显式共享池关联字段，用于消除多计划共享库存的歧义。逻辑上对应后续 plan_pool_link；同一来源计划的池关系改变需明确版本。此字段不是总设计原始必需列的逐字复制，已作为本项目 v1 契约约定。

## 格式映射与演进

- XLSX 对应同名工作表，JSON 对应同名数组，字段及自然键不变；G0 尚不解析这两种格式。
- G0 用 rate_plans.csv 统一完整 CSV 测试包；总设计示例 rate_plans.json 的适配在后续格式解析阶段实现。
- delta 缺行不变；snapshot 仅在覆盖完整、合法时才能推断删除。G0 不激活任何批次。
- 当前校验器要求五类文件均存在，包内引用可解析；partial delta 依赖当前数据库的引用及 source_sequence 比较在后续接入流程实现，不能用当前完整包限制冒充所有增量契约。
- 新必填列、自然键或语义改变需新版本；兼容性变更也必须同步字典、fixture、expected 与测试。
- 完整性校验先于记录业务检查；整包错误优先返回。行/分区错误带定位与修复动作，不能自动丢弃后激活。

## 错误码

| code | 含义 | 分类 |
| --- | --- | --- |
| MANIFEST_INVALID / MANIFEST_SCHEMA | 无法读取或不符合 Schema | 整包 |
| INVALID_SCOPE / SCOPE_ENTITY_MISMATCH | 日期范围或实体集合不合法 | 整包 |
| DUPLICATE_FILE / UNSUPPORTED_FILE / INCOMPLETE_PACKAGE | 文件清单不合法 | 整包 |
| HASH_MISMATCH / FILE_INVALID | 完整性、编码或读取错误 | 整包 |
| REQUIRED_COLUMN / INVALID_COLUMNS | 表头缺失、重复或多余 | 整包 |
| REQUIRED_VALUE / INVALID_VALUE / COLUMN_COUNT | 行字段、类型、值不合法 | 分区阻断 |
| DUPLICATE_NATURAL_KEY | 同粒度重复，不静默取最后一条 | 分区阻断 |
| PARENT_NOT_FOUND / POOL_NOT_FOUND / OUTSIDE_SCOPE | 引用或范围不合法 | 分区阻断 |

报告 row 采用 1 起始，表头为第 1 行；复杂多行 CSV 的 row 指解析记录序号而非物理文本行号。G0 无持久化 processing_run，CLI 报告不生成虚构 run_id。API 错误另外包含 trace_id。
