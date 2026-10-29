# G2 事件契约与投递

存储信封：id(UUID event_id)、tenant_id、event_type、aggregate_id、aggregate_version、payload(JSON)、trace_id、created_at。业务字段由DB触发器禁止更新；仅attempt/next_run_at/delivered_at/error为投递元数据。

| 类型 | aggregate | 效果 |
| --- | --- | --- |
| processing.created / processing.completed | processing_run | 确保唯一流程，并按持久化当前状态推进 |
| source.activated | processing_run | 更新generation依赖，旧未完成来源流程STALE |
| mapping.decided | supplier_entity_version | 校验当前映射版本，唤醒依赖该activation的流程；旧版本记STALE |
| approval.decided | offer_snapshot | 唤醒相同snapshot流程；再次读取当前审批和依赖 |
| task.updated | publish_task | 唤醒相关流程，核验成功才COMPLETED |
| workflow.resume | workflow_run | 显式重新验证当前依赖，不伪造人工决定 |

不是全局有序日志。投递至少一次，消费者以(tenant,event_id,consumer=workflow-v1)去重；inbox插入与任务创建同事务。消费后未确认会重复投递，同ID无第二次效果。前置状态未就绪保持wait；迟到事件不能降低当前ACTIVE或绕过审批。

relay短事务认领、独立消费事务、再标记送达；故障等待1秒，最多5次；耗尽保留原事件，管理员显式retry重开预算，不改ID/payload。多relay允许同事件重入，靠inbox唯一约束保证单次消费者效果。不存在跨系统“恰好一次”保证。
