# Hotel Project · G5 安全、观测、审计与恢复

本机合成酒店分销演示平台，已实现G0基础、G1纵向链路、G2数据与工作流可靠性，以及G3双模拟渠道发布、静态库存分配、分类恢复、三方对账和停卖。设计目标与实际证据分别记录，G4新增实际本地多语言模型辅助、独立预授权策略与常规更新自动接续；当前不接真实OTA、外部模型服务、订单或支付，不承诺实时有房或真实防超售。

## 环境与运行

唯一后端环境：`D:\miniconda3\envs\hotel_project`，Python3.12.15，全部入口`python -s`。PostgreSQL18.4仅127.0.0.1:55432；当前核心迁移022，SIM-A历史v1保留并追加G3协议，SIM-B有独立库和角色。Python与前端依赖分别锁定requirements.lock.txt、apps/admin/package-lock.json。

```powershell
cd D:\hotel
.\infra\scripts\g5.ps1 stop
.\infra\scripts\g5.ps1 setup
.\infra\scripts\g5.ps1 start
.\infra\scripts\g5.ps1 status
# 先显式准备公开模型（正常启动不下载）
.\infra\scripts\g5.ps1 prepare-model
# 完整隔离验收
.\infra\scripts\g5.ps1 test
# 独立正常服务的真实HTTP发布与关闭
.\infra\scripts\g5.ps1 demo
# 冻结合成数据上的实际模型评估
.\infra\scripts\g4.ps1 eval
```

已有G0/G1/G2基础环境直接升级；全新机器先按docs/runbooks/local-development.md准备基础环境和依赖。不要同时启动G1 dispatcher或G2 publish lane。G4启动两个独立分销Worker、G2 parse/control lane及独立G4编排/AI Worker。G4启动默认允许准备好的本地模型；启动前设置G4_AI_ENABLED=false可禁用，人工业务仍可运行。

访问[操作页](http://127.0.0.1:8000/admin/)及[接口文档](http://127.0.0.1:8000/docs)。API8000、SIM-A8001、SIM-B8002。PID/日志在.runtime，停止前核验所属模块和专属解释器。服务与数据库默认仅本机访问。

## 核心流程

1. 文件与来源/G2流程页：CSV/XLSX/JSON原件、sha256、流式暂存、检查点、坏行及显式分区CAS激活。
2. 实体映射：先酒店，再房型/计划，硬冲突不可强行批准。G2历史单渠道链保留。
3. 双渠道与对账：接管来源并实际核查旧协议曝光，必要时管理员关闭；冻结静态额度，明确选商品计划，分别编译渠道快照。
4. 独立审核账号批准确定hash/输入版本。SIM-B首版只支持精确NONREF，不将72小时取消改成简单布尔退款标记。
5. 运营提交双渠道计划，持久化plan/branch/chunk/cell结果；202仅受理，回执与实际读取分开，完整核验才VERIFIED。
6. UNKNOWN查询原key，账号共享限流/有限退避；永久拒绝定位到日期，不重发其他成功渠道。漂移修复限原审批明确授权且目标完全相同。
7. 管理员紧急停卖，旧售卖/修复不能复活；实际关闭后才STOPPED。部分日期关闭保留未选日期。恢复须关闭后新快照、新独立审批及显式新开售。

共享池4间、缓冲1间示例分A2/B1；多个计划不重复累计。未知曝光和未确认降额仍保留，先减确认再增；实际库存超过安全容量会冻结新增额度并安全关闭受影响日期。

G5增加“风险与审计”页、持久风险待办、有界只读回放、脱敏证据导出及受控策略换版。独立风险采集 Worker 每5秒更新；正常运行启用进程级外部网络访问保护。三库备份、恢复新库及旧来源隔离工具详见手册。本机范围验收完成：306项回归、50次业务更新、60分钟持续运行、两类恢复及两次隔离复现通过。独立Windows复现与录像按用户决定待补，未签原计划全项退出；历史恢复演练的未决风险仍在工作台显示，详见验收报告。

## 身份与安全

口令在本机.env的DEV_LOGIN_PASSWORD，不纳入仓库或报告。租户A包括a.operator、a.reviewer、a.tenant_admin、a.auditor，B对应b.前缀。开发登录可关闭，JWT真实时间不冻结；演示业务时钟2027-01-01显式标识。令牌仅页面内存保存，过期/刷新重新登录。

旧SIM_A_TOKEN/SIM_B_TOKEN分别属于旧SIM-A的租户A/B，并非两个渠道。G3凭证按渠道×租户单独配置。业务表FORCE RLS、复合外键、资源鉴权，历史快照/回执/审计保护；核心角色不使用模拟库角色，协议走真实HTTP。正常故障接口关闭，仅隔离测试入口及独立控制凭证可用。

G5完整自动验收使用hotel_project_g5_test、hotel_project_sim_a_g5_test、hotel_project_sim_b_g5_test，精确库名保护故障入口；不删除或重置开发库。测试结束恢复普通模拟器和原本运行的服务。G1/G2独立测试入口排除G3用例，完整G3套件包含兼容回归。

## 文档与实际证据

- [G5当前实施与验收报告](docs/acceptance/g5-report.md)：实际结果、失败记录及待完成项。
- [G5全项场景矩阵](docs/acceptance/g5-scenario-matrix.md)：逐项证据，不以测试数量替代场景验收。
- [G5本机运行手册](docs/runbooks/g5-local.md)、[备份恢复](docs/runbooks/g5-backup-and-recovery.md)、[审计与风险处置](docs/runbooks/g5-audit-and-operations.md)、[交付与复现](docs/runbooks/g5-reproduction.md)。

- [G5详细实施计划](G5阶段详细实施计划.md)：含架构流程、工作包及50项验收场景；48项本机范围通过，47独立Windows和49录像保留待补。
- [G4实施与验收报告](docs/acceptance/g4-report.md)：201项完整回归、60组冻结难例、真实模型与自动流转、桌面/窄屏完整操作及明确范围限制。
- [G4全项证据矩阵](docs/acceptance/g4-scenario-matrix.md)：50项逐项结论、原始证据和口径限制。
- [G4实际模型评估](docs/acceptance/g4-ai-eval.md)：冻结合成数据、规则对照及NONE误建议限制。
- [G4本机手册](docs/runbooks/g4-local.md)：启动、模型准备、禁用与隔离验收。

- [G4详细计划](G4阶段详细实施计划.md)：实际AI辅助、授权策略、自动流转与50项验收规格；已实施并完成本地限定范围全项验收；完整回归201项及独立60组冻结难例通过，详见G4报告和50项矩阵。
- [G3详细计划](G3阶段详细实施计划.md)：工作包、架构/流程图、40项场景与退出门槛。
- [G3验收报告](docs/acceptance/g3-report.md)：实际测试、证据映射与范围限制。
- [G3性能基准](docs/acceptance/g3-benchmark.md)：100/1000/10000真实日期格的协议吞吐、查询与进程内存。
- docs/acceptance/g3-tests.xml、g3-faults.json、g3-http-smoke.json、g3-browser*.png：机器与浏览器证据。
- contracts/openapi/g3.json；contracts/distribution/v1、reconciliation/v1、channels/sim-a/v2、sim-b/v1：实际契约。
- docs/runbooks/g3-local.md、g3-unknown-and-dead-letter.md、g3-reconciliation.md、g3-stop-sell.md：操作及恢复。
- docs/decisions/ADR-017～021：渠道、库存/版本、配额恢复、对账与停卖决策。
- docs/acceptance/g0-report.md、g1-report.md、g2-report.md：前阶段历史验收；G2基线93项另有G3开工复核记录。

G3固定S-A演示来源、单物业/单来源计划、USD两成人、双独立模拟渠道。SIM-B异步由operation查询推动，远端采用有界完整JSON读取，尚无真实分页协议。协议基准不包含核心报价编译/额度/Worker队列及数据库服务内存，不代表生产SLA。任意复杂取消阶梯、跨供应商共享库存合并、大视图容量、真实渠道均未因此证明，后续另阶段处理。

G4实际模型只对冻结的酒店候选排序，房型/计划保持确定性规则，新映射始终人工。管理员提议、独立审核批准策略后，常规更新逐快照形成SYSTEM_POLICY决策；超范围持久等待。模型评分不是正确概率，测试集NONE误建议较规则更多，不能用于自动合并。G4测试另用hotel_project_g4_test及两个独立G4模拟测试库，完整证据和未完成项以G4报告为准。
