# G3 本机启动与演示

后端唯一环境：`D:\miniconda3\envs\hotel_project`。所有入口使用`python -s`。沿用已有G0/G1/G2环境；全新机器先按既有基础手册准备Conda/数据库/种子与前端依赖，再执行G3准备。

```powershell
cd D:\hotel
.\infra\scripts\g3.ps1 stop
.\infra\scripts\g3.ps1 setup
.\infra\scripts\g3.ps1 start
.\infra\scripts\g3.ps1 status
# 全套回归，临时切换到独立G3测试库，结束恢复普通服务
.\infra\scripts\g3.ps1 test
# 真实登录+独立后台进程：发布并核验两渠道，再紧急关闭并核验
.\infra\scripts\g3.ps1 demo
# 额外端口8003/8004和独立测试库的协议吞吐实测，约需十余分钟
.\infra\scripts\g3.ps1 benchmark
```

操作页：http://127.0.0.1:8000/admin/，进入“6 双渠道与对账”。正常服务：API8000、SIM-A8001、SIM-B8002、两个G3 Worker及G2 parse/control两个lane；不再启动G2 publish lane或G1 dispatcher。关闭脚本校验PID模块与专属解释器，日志在.runtime。

开发库：hotel_project、hotel_project_sim、hotel_project_sim_b。测试：hotel_project_g3_test、hotel_project_sim_a_g3_test、hotel_project_sim_b_g3_test。测试与故障程序不删除或重置开发数据，历史测试证据保留。contract-test/fault-test目前同test执行完整套件，不是只跑一个小子集。

## 页面操作

1. 在G2流程页上传/激活来源，完成酒店、房型和必要计划映射；也可使用演示命令新建的虚构来源。所有账号口令从本机.env的DEV_LOGIN_PASSWORD读取，不写到报告。
2. 在G3选择来源，接管并核查旧协议曝光。CLEAR/CLOSED才能冻结额度。EXPOSED需管理员显式关闭旧协议并实际确认；UNKNOWN先排查连接，不强制放行。
3. 默认buffer1，A2/B1；每池每日总量不得超出source-buffer。库存降额未确认前，旧曝光不会释放给另一渠道。
4. 选择额度版本、显式来源计划编码。演示双渠道用NONREF；SIM-B不能无损表达FLEX72h，需正确阻断而不是随意删政策。
5. 分别编译A/B快照，切换a.reviewer独立审批。可明确勾选有限原样重放授权；不授权时不能自动修复普通漂移。新价格/额度/开售仍需新审批。
6. 切回a.operator，明确勾选两个已批准快照，提交一个计划。查看各分支、chunk、每格结果、写入/查询计数和回执。APPLIED不是最终核验；202不是完成。
7. 打开listing，按需对账；周期每60秒自动扫描。差异修复受当前目标与原授权约束。只读审计账号可查看证据，不能写命令。
8. 管理员确认范围与原因后紧急关闭。只有远端实际不可售才能确认；A关而B未知时不能声称整体完成。部分日期关闭保留未选日期状态，展示目标核验，不能解读为全listing关闭。
9. 恢复须关闭之后新编译/新独立审批的快照、显式reopen和更高版本；恢复账号或派发不会解除关闭栅栏。首版部分关闭后若需整份重新开售，先明确完整关闭并核验，再走新审批流程。

故障开关正常为false，普通业务凭证不能打开。测试控制与真实业务权限分开；真实OTA不在当前注册表中。
