# ADR-009：G0 实施基线

状态：已采纳，2026-10-26。

沿用总设计 ADR-001～008：本地文件、模块化单体、AI 只提案、不可变快照、来源选择及保守分配、独立模拟渠道、PostgreSQL 任务设施、规则基线与困难案例优先。

新增实施决策：

- 使用 Python 3.12.15、FastAPI、psycopg 3。G0 SQL 明确且规模小，暂不引入 ORM；应用事务统一通过 Database 管理。
- 使用顺序 SQL 迁移 + PostgreSQL 事务 + advisory lock + migration_history checksum，避免修改已执行迁移。后续复杂迁移需要时再评估 Alembic，不并行维护两套迁移机制。
- 用户最新要求优先：环境固定 `D:\miniconda3\envs\hotel_project`，替代计划原建议名 hotel-distribution。
- Docker 引擎在当前环境不可用，采用同一专属环境内的 PostgreSQL 18.4，本地项目目录存数据，仅监听 loopback，不更改系统服务。
- 使用开发专属签名令牌及本地演示口令，认证接口可通过配置完全取消。后续生产接入应替换为可信 IdP，不把此适配器推广为生产认证。
- 物理表仅覆盖 G0 基础；后续关系保持文档契约，不提前实现占位业务成功接口。
- 文件支持目前为完整受控 CSV 包。JSON/XLSX 使用同一逻辑字段契约，但解析与部分 delta 外部引用解析留到后续阶段。
- 全局环境中存在 POSTGRES_PASSWORD，已改为 HOTEL_POSTGRES_PASSWORD，防止本地初始化口令冲突。

重审触发：真实登录接入、数据库部署环境改变、迁移复杂度显著增加、进入多实例任务调度。变更不能降低租户隔离、快照不可变或审批边界。
