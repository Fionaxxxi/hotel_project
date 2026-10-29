# G5 备份恢复验收

schema022三库联合备份完成后，以两个新命名空间分别验证远端领先、远端已停卖。核心与两模拟库库存hash一致，完整恢复/远端核查/新业务RTO分别33.953秒与30.453秒。实际新STOP与版本栅栏阻止旧状态复活；UNKNOWN状态不能自动放行。详见[当前两类恢复](g5-restore-summary.json)及其指向的独立恢复、核查和业务报告。

RPO按备份完成时间及已知备份后sequence3计算，不称零数据丢失；实时远端不随核心回退。旧来源先隔离，受控关闭并核查后才可解除，新开售仍需新快照及独立审批。开发库、旧证据和原审批记录均不重置。

[真实备份失败演练](g5-backup-failure-drill.json)使用表锁超时，失败目录保留且不出现COMPLETE，服务恢复；磁盘不足及错误进程标记守卫由最终回归验证。Windows每日02:00任务实际动作exit0，见[调度证据](g5-backup-schedule.json)，当前配置要求交互用户登录。

备份与主机同盘，不证明整盘灾备；[两次本机隔离复现](g5-local-reproduction.json)共享专属Conda及缓存，独立Windows按用户决定待环境。历史恢复来源的未决停卖/死信仍保留，见[风险结案](g5-soak-closure.json)。操作步骤见[备份恢复手册](../runbooks/g5-backup-and-recovery.md)。
