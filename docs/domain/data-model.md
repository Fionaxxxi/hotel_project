# G0 数据模型与后续迁移

当前物理模型由 `infra/migrations/001_foundation.sql` 定义：tenant、app_user、membership、supplier、property/property_version、room_type/room_version、rate_plan/policy_version、audit_event。app_user 为全局身份，membership 的 tenant+user 唯一；租户业务实体包含 tenant_id，跨表以 tenant_id+id 引用。

current_version 外键包含所属实体 ID，延迟到事务提交检查，支持先插入实体再插入版本、最后更新指针的顺序。供应商 code 在同租户唯一；酒店 name 不唯一。版本号在同实体内唯一。

金额、库存尚未物理落表，文件契约与纯函数已定义其粒度。G1 按纵向闭环补充 artifact/import/staging/source activation、supplier entity/version、分表 mapping、inventory/rates、offer/snapshot/approval、channel listing/receipt；G2 再完善分区 CAS、任务租约、outbox/inbox 和 workflow run/step/wait；G3 完善对账与故障回执。

基础 tenant/app_user/membership 为受控元数据；普通应用无写入权。管理租户与成员的业务命令不在 G0 范围。
