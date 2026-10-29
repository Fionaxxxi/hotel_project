# SIM-B 独立协议 v1

独立服务127.0.0.1:8002、独立库hotel_project_sim_b、独立hotel_sim_b_app角色及渠道×租户凭证，不读取SIM-A存储。身份、幂等、manifest、cell键与/g3基本形状见SIM-A G3契约。

区别：

- 先CONTENT建/确认内容，再写日期格。每请求最多10格，写配额2次/秒/账号，远端DB令牌桶也执行限流。
- wire取消政策严格为`{"refundable":false}`，只与内部NONREF/0小时/stay_total无损映射。内部FLEX72小时不能偷偷转bool。比较时恢复规范化NONREF对象，wire hash与业务hash分开。
- 日历写入返回202/PROCESSING与operation_id；通过原客户端request key查询可恢复丢失operation_id。约150ms后查询推动独立远端事务完成；这是演示异步模型，不是另行常驻远端消费进程。
- 可对每格返回APPLIED或REJECTED，临时拒绝与永久政策拒绝分别编码。已成功格不重写；后续未完成格使用引用原目标的新delivery revision，原尝试仍原key。
- 每次查询终态稳定；未接纳查询404不自动证明未执行。远端实际读取与operation不相互替代。

故障控制需G3_FAULTS_ENABLED=true、相应账号Bearer和独立X-Control-Token；只由测试入口开启。支持429/500、认证、提交后延迟、异步操作丢响应、部分单元临时/永久拒绝、真实执行屏障、读取失败、不完整观察、漂移、缺失、远端领先。普通API或仅业务凭证不能启用控制。
