# ADR-012：独立模拟器与 G1 派发边界

日期：2026-10-26。状态：已实施。

SIM-A 是独立 FastAPI 进程和 hotel_project_sim 数据库，独立 migrator/app 角色。核心运行角色不能连接模拟库，反向也没有核心表权限。可共用同一 PostgreSQL 实例；本机 .env 和操作系统仍是可信开发管理边界，不宣称实现生产级进程密钥隔离。

核心发布事务持久化 publish_task 和命令幂等记录，返回202。单独派发进程通过 advisory lock 保证当前数据库只有一个 G1 dispatcher。它不使用内存回调作为唯一派发依据。网络请求不持有业务事务，发送前复核授权。

远端幂等键绑定租户、账号、酒店bundle、修订及chunk0。模拟器事务原子写入 listing 与 operation，拒绝相同 key 异内容、旧 revision 和同 revision 异 hash。所有查询按账号隔离。核心仅通过 HTTP 查询回执及内容，revision/hash匹配且重算内容hash一致才标记VERIFIED。

超时/断连进入UNKNOWN；启动时遗留SENDING变为UNKNOWN并查询原操作，不盲目重发。G1自动查询最多留存5次回执，仍未知则停在显式待处理状态，可人工继续查询；不提供不安全的换key重发。多实例租约、自动重试预算、outbox/inbox、双渠道对账与停卖属于G2/G3。

端点固定为loopback:8001，不接受业务请求中的任意endpoint，禁用HTTP环境代理与重定向。落地后延迟响应故障只在隔离验收时启用，测试结束自动关闭。
