# G5 本机实施与验收报告

> 日期：2026-10-29；结论：**PASSED_LOCAL_SCOPE_NEW_MACHINE_PENDING**。
> 范围：本机合成S-A、USD两成人、NONREF、双独立模拟渠道。独立新Windows复现按用户确认待环境；不把本机新目录冒称新机器。
> 演示录像：用户于2026-10-28明确选择不录屏，列为待补交付项；浏览器操作和截图另行验收。

## 实际验证

| 项目 | 结果 |
| --- | --- |
| all_local_p0_scenes | 通过 |
| complete_regression | 通过 |
| final_normal_services | 通过 |
| normal_load_50 | 通过 |
| actual_ai_queued | 通过 |
| continuous_60_minutes | 通过 |
| query_100_samples | 通过 |
| three_format_parse | 通过 |
| protocol_three_sizes | 通过 |
| browser | 通过 |
| audit_20_stratified | 通过 |
| two_restores_and_rpo_rto | 通过 |
| daily_backup_action | 通过 |
| local_isolated_reproduction_twice | 通过 |
| secret_scan | 通过 |
| handoff_bundle | 通过 |
| actual_cross_process_trace | 通过 |
| 100_duplicate_reorders | 通过 |

完整回归：{'tests': 306, 'failures': 0, 'skipped': 0, 'seconds': 460.705}。历史基线261项单独保留；专项与完整套件可能重叠，不相加冒称一次执行。

## 变更与失败处理

新增租户内持久告警、跨异步关联、真实JSON日志、只读有界审计与脱敏导出、策略换绑、三库一致性备份、恢复范围栅栏、受控关闭/解除及隔离复现工具。

早期持续运行及混合负载发生真实超时。原始失败保留，先修正重复安全扫描，再通过分层复测确认周期对账重复积压、新目标核验排在旧查询后，以及可空OR领取查询的历史数据退化。最终采用持久到期扫描、读取合并、G3_VERIFY前台核验、公平后台机会、STOP优先和匹配索引；没有通过延长原120秒等待预算隐藏失败。
双Worker还暴露外盒消费者收件箱的并发唯一键冲突：改为数据库ON CONFLICT返回获胜者，仅获胜事务建任务；最终306项回归包含真实并发回归。额外20秒积压探针失败保留，正式工作流仍按原计划120秒预算验收，未宣称达到20秒。
隔夜补查发现单个G3 Worker因PoolTimeout退出，原始诊断保留g5-overnight-pool-timeout.txt。现在只对PoolTimeout/OperationalError记录异常类型、退避后继续；不吞业务编程错误，不标记任务成功，持久租约和栅栏继续决定重试。最终回归包含故障后同一Worker继续执行测试；60分钟数据产生于该恢复补丁之前，未冒称补丁后又跑了一小时。
工具会话中断后，PostgreSQL自行完成崩溃恢复，未重置开发库；中断和数据库未就绪尝试保留g5-validation-interruptions.json。随后完整306项曾出现重新开售25秒窗口不收敛（305通过、1失败），原始JUnit保留g5-final-305-pass-1-reopen-fail.xml。定位到外盒回补及任务范围两条查询缺少显式租户条件，原先合计耗时约30.5秒；补齐条件使用已有复合索引，未改业务、预算或断言。相同专项修复后14.92秒通过，前后SQL模板耗时记录及专项JUnit均保留；不把未经改动偶然通过的40.34秒复跑作为根因解决证据。

## 性能、恢复与证据口径

正常负载摘要：{'accepted_updates': 50, 'verified': 50, 'failed': 0, 'p50_seconds': 11.67949999999837, 'p95_nearest_rank_seconds': 27.328999999997905, 'max_seconds': 34.34299999999348, 'stop_count': 10, 'stop_max_seconds': 5.26600000000326}。
持续验证：PASSED；实际秒数 3603.7029999999795。

解析、协议、查询与完整业务链分别报告。协议万格完整JSON实验与普通业务共享PostgreSQL时的饱和竞争单列，不外推生产SLA。模型只排序，新映射继续人工；G4的NONE误建议限制保持。

恢复不回退实时模拟器，不伪造原批准或applied。旧来源先HELD，实际新STOP核验后受控解除；新开售仍需新快照、新独立审批及reopen。RTO包含恢复/核验/新业务；同盘包不证明整盘灾备。

## 关键实测与保留风险

- 正常业务50/50 VERIFIED；P50 11.6795秒、P95 27.329秒、最大34.343秒；10次混合模型任务中的双渠道关闭最大5.266秒。
- 持续运行3603.703秒，60次真实更新、311个资源样本、失败0；最终双渠道61/61且STOPPED。本次来源无遗留任务、无新增死信。唯一PID进程RSS合计首2070.92MiB、末1908.14MiB、最大2255.61MiB；包含数据库与模型，不等同独占物理内存。
- 全局死信14→18，新增4条均属于恢复演练旧来源G5-3fe0d250：两条停卖超时、两条发送预算耗尽，旧来源仍STOP_SELL_REQUESTED，保持风险可见，未伪造远端关闭。详见g5-soak-closure.json；本次通过不等于历史风险清零。
- 两次当前022备份恢复RTO分别33.953秒、30.453秒；三库库存hash一致，分别验证远端领先与远端已关闭。RPO按实际备份与业务时间戳说明，不宣称零丢失。每日02:00任务已实际执行exit0；当前交互用户需保持登录。
- OpenAPI 75个受保护操作验证鉴权/只读权限；100次固定seed重复乱序请求实际验证远端无重复效果。当前配置秘密精确扫描通过，未知历史秘密不在认证范围。
- 20条预先限定范围的分层实际审计链通过，非随机总体抽样。早先历史链8条超过400节点上限的结果保留；随观察历史增长，旧快照可能再次截断，应选择具体批次入口，页面不把部分结果称完整。
- 两次本机新目录、三独立库复现与独立npm构建成功，业务载荷hash相同。均使用hotel_project已有Conda环境，不能代替干净Windows或全新Conda安装。子进程文本捕获改为UTF-8后，两份前端重新构建通过。
- 浏览器实操覆盖策略换版、409刷新重试、键盘确认、双渠道关闭、只读导出、390窄屏和1280桌面、恶意文本、部分结果与跨租户隐藏。补齐无渠道商品时的操作指引，最终前端构建通过。

## 入口

- [全项场景矩阵](g5-scenario-matrix.md)
- [分层性能实测](g5-benchmark.md)
- [安全验收范围](g5-security.md)
- [恢复实测说明](g5-restore.md)
- [持续运行结案与历史风险](g5-soak-closure.json)
- [机器可读验收摘要](g5-acceptance-summary.json)
- [本机运行](../runbooks/g5-local.md)
- [审计与处置](../runbooks/g5-audit-and-operations.md)
- [备份恢复](../runbooks/g5-backup-and-recovery.md)
- [复现与交付](../runbooks/g5-reproduction.md)

本机退出以所有本机检查通过及逐场景证据为准；新机器项保持待验。目标、实测、模拟和剩余边界均保留，不代表真实OTA准入或生产安全认证。
