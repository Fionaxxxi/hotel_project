# 来源文件契约 v2

实现：`services/core/g2/formats.py`、`processing.py`、`views.py`；机器入口见 manifest.schema.json。此版本为虚构演示供应商 S-A、单物业、USD、两成人、序号流 demo-v1 的固定契约。

## 文件与类型

上传 manifest.json 加五种实体的 CSV（按 scope.entities 选取），或一个 data.xlsx / data.json。XLSX 工作表、JSON 顶层数组名与 entities 精确一致：properties、rooms、rate_plans、inventory、rates。自然键/必填列沿用 `services/core/file_contract.py`。编码必须为文本，001 保持001，拒绝数字编码；JSON金额必须十进制字符串，内部Decimal到四位。日期为ISO日期，时间必须含偏移，酒店timezone为有效IANA名。公式拒绝；不接受宏文件、外链或不安全压缩内容。

CSV定位是逻辑记录号（表头后从2开始，多行引号字段不等于物理行号）；XLSX保留sheet+row；JSON保留JSON Pointer。原始文件与sha256不可变。预览每实体最多50条，问题分页最多500条，CSV问题导出最多10000条，更多使用分页API。

限制：每文件64MiB、合计120MiB、HTTP体128MiB、manifest256KiB、每实体最多200000行、日期范围366日。XLSX最多2000 ZIP条目、展开256MiB、压缩比200；不是任意Excel文档导入器。每租户最多8个待执行/执行中解析任务，超限429。

## 更新语义

- `snapshot`：scope.authority声明完整库存键前缀[property,room,pool]和价格键前缀[property,room,plan,adults,currency]。只有该集合及所选日期内缺项才形成tombstone。主数据缺行不推断删除。
- `delta`：缺行继承，upsert完整替换该自然键事实，不接受稀疏字段补丁。显式`operation=delete`仅允许inventory/rates；零库存不是删除，空值不是零。主数据级联删除拒绝。
- 稳定分区：tenant+supplier+property+demo-v1+MASTER / stay_date。日历同日价格和库存一起受控。物业锁保护generation合并，分区CAS向量保护所选日期；不相交日期可在同一旧日期向量基础上串行提交，不能丢掉彼此更新。
- 序号必须严格大于所有触及分区当前值。同序号的新命令冲突；重复已提交命令必须用原Idempotency-Key及原请求体，返回原结果。相同key异请求409。
- 部分激活必须显式selected_dates及partial_confirmed；坏日期不允许入选，MASTER/*质量问题整包阻断。缺少完整日历的合并视图可以保留供追溯，但编译仍阻断。
- 后续激活同run其他日期不重放MASTER，若当前MASTER已不同则拒绝。源run、manifest和质量问题保留。新模板建立新run，并不绕过CAS和序号。

## 持久化与兼容

原件→processing_run（parser/template/normalizer版本）→processing_record+checkpoint+DQ→immutable generation+fact→source_head。delta继承的每条事实保留原件来源，tombstone保留历史。激活命令、head/分区、审计和source.activated outbox同事务。

G1历史快照不改写；首次使用v2时采用当前G1事实作为基线，对有歧义的历史分区拒绝自动采用。兼容投影供原G1编译/映射使用，其业务原件定位回到真正来源；投影不显示为普通上传包。generation合并/投影仍是有界内存操作，最多300000事实，不宣称整条链均为常量内存。
