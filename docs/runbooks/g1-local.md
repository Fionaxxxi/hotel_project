# G1 本地运行与验收

唯一后端环境：D:\miniconda3\envs\hotel_project。所有Python入口使用-s，前端依赖在apps/admin。数据库使用现有本机PostgreSQL实例，无Docker依赖。

## 初次准备与启动

```powershell
cd D:\hotel
.\infra\scripts\g1.ps1 setup
.\infra\scripts\g1.ps1 start
```

setup安装锁定依赖、迁移核心/模拟库、追加缺少的本地随机配置、创建SIM-A独立账号/渠道编码并构建前端。不会覆盖已存在.env，不默认清库。

start依次启动本项目数据库、SIM-A(8001)、核心API/操作页(8000)、单实例dispatcher。后台进程隐藏启动，PID与stdout/stderr日志在.runtime。若有PID标记先执行stop；脚本只终止解释器路径与模块签名均符合本项目的进程，不杀占用端口的其他程序。

操作页：http://127.0.0.1:8000/admin/；核心OpenAPI：http://127.0.0.1:8000/docs。演示口令读取本机.env中的DEV_LOGIN_PASSWORD，不写入前端或提交仓库。账号a.operator/a.reviewer/a.auditor及B前缀账号沿用G0；UI通过租户/身份选择完成真实登录。令牌只在内存，过期后重新登录。

## 页面演示

1. 运营选择datasets/fixtures/F00-valid下六个文件，上传，查看来源预览，确认参数校验并激活。
2. 切换同租户审核账号，按酒店→King/Twin→FLEX/NONREF顺序批准可兼容候选。冲突按钮不可用，服务端同样拒绝。
3. 切回运营，编译快照，核对每晚USD100→110、三晚330以及两个计划共用四间池，再提交审批。
4. 独立审核账号批准快照，切回运营创建发布任务。dispatcher读取数据库任务并发送独立模拟器，页面轮询/刷新可见VERIFIED。
5. 加载来源追溯，核对原件摘要及记录编号；审计账号只能读取。

G0固定时钟样例在显式模拟业务时钟下演示；页面始终显示该状态。默认首次G1 setup在development启用演示时钟。关闭DEMO_CLOCK_ENABLED后，固定2027样例将按真实业务时间判断为未来/过期，并阻断激活或编译；JWT不使用固定时间。

## 隔离测试与真实HTTP验收

```powershell
.\infra\scripts\g1.ps1 test
.\infra\scripts\hotel.ps1 check
& D:\miniconda3\envs\hotel_project\python.exe -s -m infra.g1_smoke
```

test显式准备hotel_project_test与hotel_project_sim_test，以及.runtime/test-artifacts。只执行前向迁移与幂等种子，不默认删除/重建数据库。测试期间暂停本项目dispatcher，模拟器短暂切到测试库并启用单个故障；finally恢复正常模拟库并关闭故障模式。原来运行的dispatcher会恢复。不要在验收期间进行人工发布。

数据库测试拒绝在未启用HOTEL_G1_TEST_MODE或非指定测试库的环境运行；使用脚本而非直接pytest访问开发库。纯文件单元测试仍可单独执行tests/test_contracts.py。

g1_smoke要求正常服务已启动，通过真实HTTP和独立dispatcher跑完整闭环，在开发库追加一组独立合成演示来源，保存docs/acceptance/g1-http-smoke.json。它不会修改真实渠道。

## 停止与恢复

```powershell
.\infra\scripts\g1.ps1 status
.\infra\scripts\g1.ps1 stop
.\infra\scripts\hotel.ps1 stop-db
```

停止应用不删除原件或数据库。若进程启动失败，先检查对应.stderr.log和端口，确认PID身份后处理。单dispatcher重复启动会因advisory lock失败；不要用删除锁或关闭RLS绕过。遗留SENDING在dispatcher启动时转UNKNOWN并查原操作，完整租约接管留到G2。
