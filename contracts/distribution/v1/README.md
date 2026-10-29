# G3 发布契约 g3-flow-v1

固定为本机SIM-A/SIM-B、S-A演示来源、单来源物业的两渠道计划。USD、两成人、明确选中的来源计划编码；其他来源合同未配置时422。G2同物业generation/映射必须有效，原件血缘不变。

## 身份与版本

旧SIM_A_TOKEN/SIM_B_TOKEN属于旧SIM-A内的租户A/B，不是两个渠道。G3使用G3_SIM_A_TENANT_A/B_TOKEN、G3_SIM_B_TENANT_A/B_TOKEN，两个服务、库与角色独立。

业务目标desired_revision与远端delivery_revision分开。新价格、政策、日期、额度、关闭或新开售建立新业务目标；同一有效授权目标的有限原样修复仅增加发送序号。快照冻结compiler=channel-compile-v1、chunker=canonical-cell-v1、来源/映射/渠道编码/能力/额度版本。

命令幂等：(tenant,route,key,request_hash)。远端key包含tenant/account/listing/delivery/action/chunk；同执行同key同body，异body409。manifest包含wire content hash、批准业务payload hash、各chunk cell hash。一个pool/day一格，一个plan/day一格；不是按天数计算上限。

## 操作与授权

- operator/tenant_admin：接管来源、核查旧曝光、冻结额度、编译快照、提交计划、按需对账、暂停/恢复派发、已有授权内修复与有限预算恢复。
- reviewer/tenant_admin：独立快照批准/拒绝；不得审批本人提交。allow_repair必须显式授予，只限有效期内相同业务目标；不允许新价格、扩范围或开售。
- tenant_admin：旧协议关闭、紧急停卖、账号恢复。所有写入有理由/审计，资源绑定租户。
- auditor：只读。前端禁用不是授权边界。

## 库存

每池每日capacity=max(0,qty-buffer)，额度A+B不得超过capacity。多个计划共用一个池，不累计库存；未知来源共享关系不合并。曝光保留取已有实际/在途保守上界，降额请求、取消或普通404不能释放未知曝光。先降并核验、再转额度。

每次新写入复验desired、快照依赖、审批、原操作人成员资格、账号、额度与租约。G3接管后G1/G2禁止同来源新售卖，但允许旧任务查询；旧SIM-A整包曝光先实际读取，必要时管理员关闭并确认。关闭版本在HTTP前持久保留，后续G1编译以已保留/已观察版本为下界接续。

## 结果

plan/branch/chunk/cell分别可追溯。202仅受理；APPLIED仅回执，实际读取全部目标相符才VERIFIED。原始cell拒绝记录和后续获准修复结果分开保留；部分渠道成功不因另一边失败重发。

首次计划在任何外部发送前可取消；只支持无前序曝光的首次计划，未发送证明成立后才释放保留。已有外部效果的计划必须查询/关闭，不提供伪回滚。暂停不阻止必要查询和关闭。G3_RECON与G3_DELIVER复用G2租约/fencing/outbox/inbox；旧G2 Worker只认领自己的三类任务。
