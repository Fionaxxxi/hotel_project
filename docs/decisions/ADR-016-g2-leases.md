# ADR-016：执行租约、fencing与外部恢复

状态：采纳，2026-10-26。

执行任务以SKIP LOCKED认领，数据库时钟30秒租约、owner+递增token校验；每次提交/续租强制检查。解析每1000条续租。未到期不接管，过期可认领；旧owner迟到写入/续租均失败。重试预算耗尽MANUAL，显式恢复保留逻辑键和token历史。

默认独立parse/control/publish三进程；每轮按可信A/B租户名单公平获取至多一任务，不放宽RLS。每租户最多8个待执行解析。每listing advisory锁限制外部并发，忙/暂停等待不耗故障预算。

fencing不能撤销已经发出的HTTP。发送使用原业务幂等键及revision，结果未知或发送后进程退出时接管先查询原key，远端核验才VERIFIED。仅连接独立SIM-A；不是对任意真实OTA的恰好一次承诺。真实进程exit、提交窗口和双进程barrier测试是本阶段必要证据。
