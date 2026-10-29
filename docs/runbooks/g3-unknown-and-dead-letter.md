# G3 UNKNOWN、预算与死信

先读plan→branch→task→cell/receipt，判断是“等待许可”“未确定接纳”“确定拒绝”还是“实际内容不符”。不能将所有失败统一重发。

| 状态/原因 | 合法动作 |
| --- | --- |
| ACCOUNT_QUOTA / RATE_LIMITED | 等next_run_at/cooldown，尊重Retry-After；不额外启动Worker突破配额 |
| SENDING / UNKNOWN / PROCESSING | 按原request_key查operation；丢operation_id也查原key。404通常仍未知 |
| 确定未接纳的500/429 | 有限原key原body重试；不造新商业目标 |
| 422 / 永久cell拒绝 | 查看字段/日期，修配置并提交新快照；不抹掉已成功格 |
| 401/403 | 对应账号暂停新写入；管理员排查凭证/配置并恢复，再处理任务预算。其他账号不应暂停 |
| MANUAL / 预算耗尽 | 阅读最后回执及实际观察，消除原因后带理由和版本重开有限预算；最多3轮，历史不清零 |
| REMOTE_AHEAD | 调查远端领先原因，不通过+1抢版本覆盖 |
| LEASE_LOST | 旧owner提交已拒绝，查看接管者；禁止手动改token绕过 |

写最多6次/15分钟，查询20次/15分钟；锁/配额等待不扣故障预算。独立读预算保证确认路径，关闭作业优先但仍受账号硬配额。SOURCE_MANAGED_BY_G3表示旧派发已被接管，不应重新启动旧publisher规避。

页面预算恢复要求有效理由与当前task.version；底层接口`POST /v3/dead-letters/{id}/retry`。恢复不确定效果继续查原操作；明确未接纳的认证/限流响应才回READY。原key/body、所有回执、预算轮次和审计保留。

故障验收入口`infra/g3_fault_child.py`只允许HOTEL_G1_TEST_MODE=true且精确hotel_project_g3_test。退出81/82覆盖意图事务前后、83覆盖真实远端返回后本地提交前、84覆盖消费者提交后投递确认前。额外旧HTTP屏障用例证明“迟到执行”，不只延迟响应。
