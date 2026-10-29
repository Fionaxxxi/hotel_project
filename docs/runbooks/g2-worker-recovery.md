# G2 执行与崩溃恢复

默认三个独立Worker：parse、control、publish。PID、stdout/stderr位于.runtime/g2-worker-0/1/2.*。停止脚本验证解释器及模块后停止已登记进程，不按端口批量杀进程。禁止同时启动G1 dispatcher；它的全局启动恢复与G2租约模型不同。

## 故障处理

| 看到的情况 | 处理 |
| --- | --- |
| 解析RUNNING进程已退出 | 重启G2；等待数据库30秒租约过期，接管以检查点续跑；已提交前缀不会重复 |
| 仍有心跳 | 不强制接管，不手动改token；等待当前owner完成 |
| MANUAL / BLOCKED | 查看operations错误；修正环境或新原件。确认原因消除后显式job retry重开预算，保留逻辑key和token历史 |
| WAIT_SCOPE / WAIT_QUALITY | 人工处理范围/质量，不靠不断重试越过门槛 |
| WAIT_MAPPING / WAIT_APPROVAL | 对相同资源作合法决定，相关事件自动唤醒；检查当前source和snapshot版本 |
| REJECTED / 过期快照 | 运营显式恢复，重新校验/编译并独立审批，旧审批不沿用 |
| SENDING / UNKNOWN | 过期接管先查询原业务key；禁止新建key盲重发。只有远端内容/revision/hash一致才VERIFIED |
| LEASE_LOST | 旧owner结果已被拒绝；查看新owner任务。不要绕过fencing手动落库 |
| LISTING_BUSY / WORKFLOW_PAUSED | 正常等待，不消耗故障预算；恢复后再次检查依赖 |

pause仅暂停新的渠道派发，无法撤销已发送HTTP；在途UNKNOWN查询仍需完成。cancel拒绝SENDING/UNKNOWN/ACKNOWLEDGED/VERIFIED，以免将实际外部效果掩盖为取消。

每次提交检查owner+token+RUNNING+DB租约。解析每1000行续租，短控制事务提交前再检查；不提供线程持续续租覆盖无限长操作。长事务超过租约会安全拒绝并有限重试，不能据此声称无限容量。

真实故障入口`infra/g2_fault_child.py`只允许hotel_project_g2_test并要求HOTEL_G1_TEST_MODE=true；HTTP请求不能启用故障。`g2.ps1 fault-test`目前执行完整套件（含故障），不会在开发库注入退出。测试强制缩短到期时间以加速接管，不代表实际等待30秒的耗时基准。
