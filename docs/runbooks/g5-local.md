# G5 本机运行与验收

唯一 Python 环境是 `D:\miniconda3\envs\hotel_project`，入口始终附带 `-s`。G5保留G0～G4业务，新增租户内运行指标、持久告警、只读证据回放、策略换绑操作以及隔离恢复工具。验收结论以最终报告为准，脚本存在不代表相应场景已经通过。

```powershell
cd D:\hotel
.\infra\scripts\g5.ps1 stop
.\infra\scripts\g5.ps1 setup
.\infra\scripts\g5.ps1 start
.\infra\scripts\g5.ps1 status
```

API8000、SIM-A8001、SIM-B8002、PostgreSQL55432，均为本机监听。G5启动两个G3分销Worker、G2解析/控制、G4编排/AI及G5告警采集。旧G1 dispatcher和G2 publish lane不并行运行。进程停止同时核验专属解释器、精确模块参数及工作目录；错误PID保持可见并拒绝终止，不按端口杀进程。

正常启动默认启用已准备的本地模型；如需停用，在启动前设置 `$env:G4_AI_ENABLED='false'`。独立准备模型使用 `g5.ps1 prepare-model`，正常启动不下载。服务配置记录在私有 `.runtime/g5-service-profile.json`，备份恢复服务时保留模型启用状态。

开发登录必须有 `DEV_LOGIN_PASSWORD_HASH`，setup从既有本机演示口令生成PBKDF2校验值，不更换实际口令。服务不回退到明文验证；排除在交付包外的.env仍保留旧演示驱动使用的口令及其他本机秘密。不要共享.env。令牌只存页面内存、有效30分钟；开发登录按真实时间限速，数据库中的IP/账号摘要桶跨进程共享。联网生产身份系统未在本阶段实现。

G5正常启动设置进程级 `G5_OFFLINE_ONLY=true`：Python出站套接字仅允许loopback，数据库配置只允许本项目127.0.0.1/localhost及55432，远程地址和hostaddr绕过被拒绝。这是应用内保护，不是操作系统沙箱，也不是关闭电脑网卡。固定模型子进程继续剥离凭证并使用离线缓存。

## 操作入口

工作台第7项“风险与审计”显示租户内队列、风险告警、只读回放与策略换版。确认告警只表示接手，后台再次成功采集并确认风险消失后才恢复。原操作UNKNOWN时继续查询原key；实际止损仍在“双渠道与对账”执行明确范围的停卖。

策略换版先暂停旧版本，独立审核并启用同范围新版本，选择订阅后复核范围、基准、开关和版本，再提交CAS换绑。旧机器决定不能沿用；换绑不解除停售栅栏。

```powershell
# 隔离的完整回归；不重置开发库
.\infra\scripts\g5.ps1 test
.\infra\scripts\g5.ps1 security-test
# 正常实例上的真实合成业务演示
.\infra\scripts\g5.ps1 demo
# 查询、解析、协议、完整链、持续运行分别测量
.\infra\scripts\g5.ps1 benchmark
.\infra\scripts\g5.ps1 parse-benchmark
.\infra\scripts\g5.ps1 protocol-benchmark
.\infra\scripts\g5.ps1 load-test
.\infra\scripts\g5.ps1 soak -DurationSeconds 3600
```

解析基准使用 `hotel_project_g5_benchmark` 和独立.env文件；完整回归使用 `hotel_project_g5_test` 及两个G5模拟测试库；协议基准另起8003/8004。它们仍共享本机PostgreSQL进程和磁盘，分层基准应分开运行。不要将万格完整JSON协议压力作为正常业务基准的隐含背景负载；如故意混跑，必须独立报告失败、资源竞争和场景定义。

完整链负载生成明确的合成来源，不删除开发历史。中断试验需按证据中确切的G5来源范围暂停、关闭并保存观察。旧重复对账仅可在停止项目派发后运行 `python.exe -s -m infra.g5_compact_recon` 合并；记录改为CANCELLED并保留，不重发发布任务。

## 文件与故障

普通JSON日志位于 `.runtime/logs`，每文件10MiB、保留3轮。凭证、原文和异常堆栈不写入普通业务日志。全局连接池统计只在本机日志中，不通过租户指标接口暴露。任务与不可变审计记录在数据库中，不依赖日志留存。

主脚本test会短暂停止普通服务，使用隔离测试库和故障模拟器，finally恢复正常故障关闭配置与此前运行状态；因此不能与持续运行验收同时执行。服务启动需已完成迁移；live仅证明进程存活，ready还检查数据库和精确schema版本。

G3 Worker遇到连接池临时超时或数据库OperationalError时，只记录异常类型并退避1秒后继续轮询，不修改任务成功状态；原事务回滚，持久租约、原key和版本栅栏继续生效。数据库持续不可用时，心跳陈旧及未知状态应保留可见。其他错误仍暴露，检查status和对应项目日志，不因API存活就推断Worker全部正常。

恢复和日常备份见[g5-backup-and-recovery.md](g5-backup-and-recovery.md)，交付/复现见[g5-reproduction.md](g5-reproduction.md)。
