# G4 本机运行与验收

后端只用`D:\miniconda3\envs\hotel_project\python.exe -s`。先按既有基础手册准备Conda、Postgres和锁定依赖；新增依赖也只安装于hotel_project。

```powershell
cd D:\hotel
.\infra\scripts\g4.ps1 stop
.\infra\scripts\g4.ps1 prepare-model
.\infra\scripts\g4.ps1 setup
.\infra\scripts\g4.ps1 start
.\infra\scripts\g4.ps1 status
.\infra\scripts\g4.ps1 test
.\infra\scripts\g4.ps1 eval
.\infra\scripts\g4.ps1 demo
```

G4 start首次未指定进程环境开关时启用已准备的本地模型；不自动下载。需要禁用则启动前明确`$env:G4_AI_ENABLED='false'`。正常FastAPI单独启动的默认仍是模型关闭。两个模式都需要人工映射和独立批准策略；租户授权策略默认关闭，不因模型启用获得业务权限。

API8000、SIM-A8001、SIM-B8002；两个G3分销Worker、G2解析/控制Worker、G4-flow和G4-ai各一。不要另启旧G1 dispatcher或G2 publish lane。G4脚本负责停止全部项目服务，核验PID所属模块和专属解释器；日志在.runtime。

测试使用hotel_project_g4_test、hotel_project_sim_a_g4_test、hotel_project_sim_b_g4_test，精确库名保护故障程序，不重置开发数据。test临时启用故障接口，结束恢复普通服务。contract-test/fault-test目前同test跑完整套件。G3历史协议基准不属于G4验收命令，不能当作G4完整业务链或AI容量指标。

页面：http://127.0.0.1:8000/admin/，G4建议、策略及运行证据位于“5 流程与质量”下方；G3发布/审批/关闭仍在“6 双渠道与对账”。演示身份与口令沿用.env，不在报告保存凭证。JWT仅页面内存，刷新需重新登录。

demo追加一组虚构来源：实际模型建议→人工初始映射/基准→独立策略批准→自动小幅更新→暂停/恢复→越界等待→人工快照批准后恢复→两边实际STOPPED。`G4_EXPECT_MODEL_DISABLED=true`可核验禁用服务时规则/人工闭环。正式证据见docs/acceptance/g4-report.md。
