# G2 事件积压与恢复

业务变更和outbox同事务。relay投递至少一次；inbox去重和消费者效果同事务。消费者提交后、delivery确认前进程退出可安全重投，event_id不变。所有操作使用租户上下文，Worker仅遍历可信配置HOTEL_WORKER_TENANTS（默认A,B），不使用迁移角色。

1. 在“流程与质量”诊断区查看未确认事件、等待点和任务错误。/v2/operations最多返回100任务；界面展示其中20条。
2. 区分正常人工wait和事件错误。未获映射/独立审批不是投递失败；先对原资源处理，不制造决定事件。
3. relay最多自动尝试5次，间隔1秒；数据库保留error类型、原事件及未确认状态。排查Worker日志与资源版本。
4. 经tenant_admin身份对/v2/events/{event_id}/retry发POST，重开原事件投递预算；已送达返回already_delivered=true。不改payload、不生成新event_id、不直接插inbox。
5. 迟到mapping版本在inbox记STALE，不唤醒错误流程；重复event_id返回DUPLICATE。唤醒后仍重读current依赖，不凭旧事件批准发送。

消费者逻辑名workflow-v1是去重边界；变更消费者语义时需版本化并明确回放迁移策略。当前单本地数据库方案不承诺跨broker、真实OTA或灾备场景的恰好一次。

审计记录包含人工命令、恢复预算与来源激活，系统投影标识system.g2-projection，流程保留原始操作人created_by；系统不冒充审核员。
