# g2-flow-v1

唯一运行键：(tenant,processing_id,definition)。PARSE / ACTIVATE / COMPILE / PUBLISH是持久化成功步骤，按(step_key,input_hash)去重，attempt另存execution_job/job_attempt。输入变化建立新的成功输出记录，既有快照业务payload不可变。

```mermaid
flowchart TD
 U[原件上传与版本化解析] --> Q{质量与范围}
 Q -->|需处理| WQ[WAIT_QUALITY / WAIT_SCOPE]
 WQ -->|人工确认有效分区| A[CAS原子激活 generation]
 Q -->|人工确认| A
 A --> M{依赖映射是否有效}
 M -->|否| WM[WAIT_MAPPING]
 WM -->|映射决定事件| M
 M -->|是| C[确定性编译并提交快照]
 C --> WA[WAIT_APPROVAL 独立账号审批]
 WA -->|拒绝| R[REJECTED 显式恢复后重新审批]
 WA -->|批准事件| P[创建唯一发布任务]
 P --> IO[SIM-A HTTP 写入或查询原key]
 IO -->|未知| WX[WAIT_EXTERNAL 有限查询]
 WX --> IO
 IO -->|远端内容核验| V[COMPLETED / VERIFIED]
 A -->|来源再次更新| S[旧待处理流程STALE]
```

wait保存gate/resource/expected_version及resolved_event_id。人工等待释放执行租约，页面关闭/进程重启不丢。唤醒后重新读取依赖、snapshot、审批与版本，事件不是授权。每个物业/listing流程独立；G2不支持一个物业内任意商品子图的局部发布。

pause停止新增外部派发，解析与已在途查询可完成；cancel仅允许尚无不可撤销外部效果的流程，不能将UNKNOWN伪装取消。resume是显式命令及事件；被拒绝/过期且未建任务的snapshot会重新编译，必须重新独立审批。没有通用截止时间调度器，审批期限在推进和派发前再校验。
