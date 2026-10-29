# Operations v1

`GET /v5/operations/metrics`、`GET /v5/operations/alerts`受身份及租户限制。指标是实时只读查询，CURRENT代表本次查询成功；不保证所有Worker健康。有限类别聚合数量与有界样本分开。全局连接池计数不出现在租户接口。

`POST /v5/operations/collect`仅operator/tenant_admin；生产采集由独立本地Worker运行。`POST /v5/operations/alerts/{id}/acknowledge`要求expected_version与非空reason，只允许OPEN→ACKNOWLEDGED。RESOLVED由成功完整采集确认，不是用户清除风险。

后台任务G3_VERIFY/G3_DELIVER前台机会与G3_RECON后台机会为4:1且可借空闲机会，STOP优先。重复读取合并，不重复产生发布效果。资源标签不包含对象ID/酒店名；对象通过权限内明细和日志定位。

本地基准预算在[performance-budgets.json](performance-budgets.json)。预算、实验规模和实际结果分开保存；失败样本与超时留在分母中。
