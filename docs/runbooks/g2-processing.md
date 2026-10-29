# G2 本地处理与联合演示

唯一环境：`D:\miniconda3\envs\hotel_project`。启动前端构建由项目Node依赖负责；后端/Worker/验收均`python -s`。不使用base。

```powershell
cd D:\hotel
# 初次准备或升级；保留历史数据
.\infra\scripts\g2.ps1 setup
# 重启时先stop，再start
.\infra\scripts\g2.ps1 stop
.\infra\scripts\g2.ps1 start
.\infra\scripts\g2.ps1 status
# 完整验收；暂时切换模拟器到独立测试库，结束恢复
.\infra\scripts\g2.ps1 test
# 真实HTTP闭环；在开发库追加虚构来源，不是只读
& D:\miniconda3\envs\hotel_project\python.exe -s -m infra.g2_smoke
# 1万/10万记录、三格式、每档两次；独立测试库追加，不重置
.\infra\scripts\g2.ps1 benchmark
```

访问 http://127.0.0.1:8000/admin/ ，进入“流程与质量”。口令在本机.env的DEV_LOGIN_PASSWORD，不复制到验收报告。A租户运营a.operator负责上传/范围确认；审核a.reviewer负责映射和独立快照审批，不能自己审批自己的提交。

1. 选择datasets/g2/equivalent/csv内manifest及五个CSV；也可xlsx/json两文件包。点“上传并提交后台处理”，等待持久化解析结束。该组三格式同物业序号依次1/2/3，不能反向演示；已执行过的包会复用，不创造新来源序号。
2. 查看已提交数、检查点和问题。完整包选三日激活；有坏日时先取消勾选坏日、核对剩余范围并勾选部分激活确认。MASTER/*错误不可绕过。新包缺基线日历时后续编译仍阻断。
3. 打开映射审核，切换审核身份后可从“流程与质量”重新打开同run。按酒店→房型→计划批准兼容目标。切换身份需要重新输入口令；刷新页面需重新登录，但后台运行不受影响。
4. 完成映射后后台自动编译/提交快照；打开独立审批，核对金额、库存、政策再批准。无需手动编译或创建发布任务。后台自动到SIM-A并核验；流程显示全流程已核验。
5. datasets/g2/delta只更新FLEX一晚115.00，售价126.50；其他日期/库存继承。该包seq4，要先完成相同DEMO-G2基线。映射复用也会重新核对语义依赖，新快照仍需独立审批。
6. 发布与追溯页可查看原始CSV记录、XLSX sheet/row、JSON Pointer、sha256、继承来源与tombstone。下载需资源授权。

API前缀/v2包括imports、processing-runs、issues分页/CSV、workflows、operations、jobs/retry、events/retry；字段见contracts/openapi/g2.json。重复激活必须保存原key及原body；409后读取最新分区向量并人工重新确认，不能自动强制覆盖。

固定限制与更新语义见contracts/files/v2/README.md。当前仅SIM-A、演示序号流及固定模板。报告导出最多10000条，更多用分页；前端问题只显示500条。大文件stage基准不代表激活/编译容量。
