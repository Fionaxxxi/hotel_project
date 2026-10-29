# G5 一致性备份与保守恢复

备份是本机运维CLI能力，包含所有本机演示租户；它不是租户管理员的HTTP导出。备份包含核心库、两个独立模拟库、不可变原件、模型manifest、源码hash、schema与迁移checksum，排除.env、明文凭证及服务日志。

```powershell
.\infra\scripts\g5.ps1 backup
.\infra\scripts\g5.ps1 verify-backup -BackupPath 'D:\hotel\.runtime\g5-backups\<备份ID>'
```

备份脚本核验并停止项目服务，记录运行配置；三库所有表同时持有阻止写入的SHARE锁后分别pg_dump，复制原件、生成逐文件hash，最后才写COMPLETE。失败包不能恢复。finally恢复之前运行的普通服务，不删除旧备份。独立备份模块拒绝仍有托管服务PID标记的情况。

同盘备份验证恢复流程，不代表防整盘损坏或异地容灾。默认不自动删除包。原件、模型缓存与备份均为本机私有文件，不应放进公开交付包。

## 日常调度

```powershell
.\infra\scripts\g5-backup-task.ps1 install
.\infra\scripts\g5-backup-task.ps1 status
# 如需移除，只移除经路径核验的本项目任务
.\infra\scripts\g5-backup-task.ps1 uninstall
```

任务名 `HotelProject-G5-DailyBackup`，每天本机时区02:00、当前用户交互会话、Limited权限、错过后可补运行、不并发重入。不会保存Windows密码、开放端口或注册系统服务。电脑未开机/没有可用会话时不能承诺按时备份；24小时陈旧告警保留风险。手动触发任务验证的是同一个执行动作，不冒充多日持续调度结果。

## 恢复到新库

```powershell
.\infra\scripts\g5.ps1 restore -BackupPath 'D:\hotel\.runtime\g5-backups\<备份ID>' -RunId 'drill_example'
```

只创建 `hotel_project_g5_restore_<run>_{core,sim_a,sim_b}` 和新原件目录，已存在目标拒绝，不drop、不覆盖开发库。先校验dump/文件hash，再比对逐表行数和排序内容摘要，恢复库默认事务只读。仅调整新库内的表/函数owner，不使用会影响共享数据库所有权的REASSIGN OWNED。

代码与备份schema不匹配时ready拒绝。旧备份应配合相应代码，或在核验新目标后显式执行前向迁移并记录升级前后差异；不得修改旧迁移checksum或将版本号直接改成当前值。新机器的角色秘密从独立安全配置提供，备份不携带登录凭证。

## 远端不能随核心库一起回退

```powershell
.\infra\scripts\g5.ps1 reconcile -RunId 'drill_example'
```

该动作读取仍在运行的真实模拟器，不修改远端。恢复前已存在的来源全部HELD，旧待执行任务取消但保留历史，旧自动策略与订阅暂停，listing暂停并提升本地stop epoch。私有许可文件只允许新独立来源进入新业务链；不能据此放开旧来源。UNKNOWN和REMOTE_AHEAD保持可见，不伪造审批、收据或applied revision。

运维在恢复实例上使用以下受权接口处理旧来源：

1. `POST /v5/recovery/listings/{id}/stop`，管理员提供当前version和理由。服务读取完整远端，要求日期格范围可核对，先记录远端revision下限，再生成一个更高revision的明确STOP；范围改变/远端未知时阻断。
2. 沿原G3 Worker实际派发和观察；确认两边新的STOP目标与实际观察一致。
3. `POST /v5/recovery/release`，提供source_key和理由。必须有恢复后新生成、完整核验的关闭证据；服务重新读取远端并核对本地CAS后才解除恢复HELD。
4. 原stop fence保留。重新开售仍需要关闭后新快照、新独立审批和显式reopen，不能沿用备份中的旧批准。

真实演练工具 `infra.g5_restore_drill` 在8005启动恢复API及独立Worker，保留正常8000核心与远端状态，并在finally终止它启动的进程。恢复结果和未决范围写入g5-restore/g5-reconcile/g5-restore-business证据文件。RTO必须包含还原、校验、远端处理以及恢复实例的新业务闭环；只有还原文件的耗时不能称完整RTO。

拟议本地目标为RPO≤24小时、RTO≤1小时；最终报告只填写实际测得的备份年龄、数据损失窗口、保守冻结范围和恢复耗时。恢复时存在HELD/UNKNOWN并不等于所有旧业务已经自动恢复。
