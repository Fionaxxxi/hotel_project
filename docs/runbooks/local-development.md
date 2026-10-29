# 本地运行与恢复

## 配置与启动

主入口：`infra/scripts/hotel.ps1`，详见 README。所有 Python 执行绑定 `D:\miniconda3\envs\hotel_project`。首次 setup 生成 .env，已存在时不会覆写。独立本地 PostgreSQL 使用 .runtime/postgres-g0 与端口 55432；API 使用 8000。

Windows 受限执行环境可能禁止启动 PostgreSQL 子进程或连接本机 TCP；应在有相应权限的本机终端运行，不改用 base 或假数据库绕过验收。脚本不申请 Windows 服务安装、不修改防火墙、不开放外网监听。

## 常见问题

| 症状 | 检查与恢复 |
| --- | --- |
| 数据库未启动 | 使用项目 Python 执行 `-m infra.local_postgres status`；检查 `.runtime/postgres.log`；执行 `start` |
| 端口已占用 | 核对对应实例与数据目录；不停止不属于本项目的进程。需换端口时同步 postgresql.conf 和 .env 三条连接串 |
| 初始化未完成 | 脚本检查 global/pg_control，不把单独 PG_VERSION 当作成功。先保留日志、确认无进程，再由维护者处理不完整目录；不自动递归删除 |
| 口令失败 | 检查本项目 .env 与集群的初始化凭据；不要输出凭据。HOTEL_POSTGRES_PASSWORD 与系统 POSTGRES_PASSWORD 独立 |
| ready=503 | 分别检查数据库连接与迁移版本；live=200 不代表数据库可用 |
| 迁移校验和不一致 | 不修改已执行 SQL，恢复原脚本或提交后续前向修复迁移 |
| 跨租户测试失败 | 先检查应用角色是否非 owner、无 BYPASSRLS，再核对事务上下文、策略与复合外键 |

## 角色与种子

bootstrap postgres 仅用于创建项目数据库与角色。hotel_migrator 持有迁移权限；hotel_app 只承担业务访问。种子只追加固定 ID，不覆盖管理员已修改的数据；需要清空测试库时应另行明确目标和备份，不提供默认破坏性重置命令。

## 测试运行约束

测试需要本项目独立开发库，不能针对共享或生产数据库运行。负测会短暂禁用 a.operator 成员关系，并在 finally 恢复；不应与人工操作或并行测试同时运行。测试中的临时迁移角色策略也在 finally 删除。

独立 Python 单元/文件测试可使用项目解释器执行 `python -s -m pytest tests/test_contracts.py -q`；完整测试执行项目脚本 test。`-s` 禁用用户级包目录，避免 Windows 用户级依赖污染。若本机 TCP 被沙箱阻止，应在允许连接的终端执行完整套件并记录实际结果。

## 停止

前台 API 使用 Ctrl+C 退出，连接池随应用生命周期关闭。数据库用 `hotel.ps1 stop-db` 进行 fast shutdown；这是 PostgreSQL 的受控事务终止方式，不删除数据。

## 后续限制

G0 未实现供应源 TTL、发布 UNKNOWN、误匹配停卖或备份恢复演练。对应 runbook 应在功能实现阶段补齐，不能仅凭本地启动手册宣称具备生产运营能力。
