# G0 实施与验收报告

日期：2026-10-26。阶段结论：**G0 基础实施完成，允许进入 G1**。本结论只覆盖 G0，不代表整个 MVP、生产安全或渠道接入已经完成。

## 实测环境

| 项目 | 实际配置 |
| --- | --- |
| 项目 | `D:\hotel` |
| 专属 Conda | `D:\miniconda3\envs\hotel_project` |
| Python | 3.12.15；执行入口显式使用 `-s`，禁用用户级包 |
| PostgreSQL | 18.4，真实本地实例；数据目录 `.runtime/postgres-g0` |
| 数据库 | `hotel_project`，监听 `127.0.0.1:55432` |
| 应用/迁移角色 | `hotel_app` / `hotel_migrator`，分离权限 |
| 应用角色属性 | 非超级用户、非表所有者、无 BYPASSRLS，已测试 |
| 迁移 | `001_foundation`；schema_revision=001；校验和记录于 migration_history |
| API | FastAPI，`127.0.0.1:8000` |
| 依赖证据 | requirements.lock.txt、environment.yml、conda-explicit-win-64.txt |
| 代码版本 | 当前工作目录无 Git 仓库，未创建提交；通过交付文件及测试 XML 留存证据 |

## 工作包完成情况

| 工作包 | 交付证据 | 状态 |
| --- | --- | --- |
| WP01 领域 | docs/domain 下词典、不变量、数据模型、状态职责；ADR-009 | 完成 |
| WP02 文件 | manifest Schema、字段字典、错误码、完整 CSV 契约校验器 | 完成 |
| WP03 环境 | hotel_project、锁文件、配置模板、PowerShell 入口 | 完成 |
| WP04 迁移 | 001 SQL、事务迁移、checksum、角色分离、RLS 与复合 FK | 完成 |
| WP05 数据 | 两租户、三供应商记录、同名酒店、King/Twin、独立政策版本、F00～F05 | 完成 |
| WP06 身份 | 开发签名令牌、subject/membership、租户状态、角色守卫 | 完成 |
| WP07 API | 五类基础只读/健康接口、开发登录、OpenAPI、错误和日志 | 完成 |
| WP08 验收 | 36 项测试、真实 HTTP 冒烟、pip check、静态检查 | 完成 |

## 执行结果

完整测试命令：

```powershell
& D:\miniconda3\envs\hotel_project\python.exe -s -m pytest -q --junitxml=docs/acceptance/g0-tests.xml
```

结果：**36 passed，1 warning，7.35 秒**。这是当前本机这一次运行的结果，不是性能基准。XML 见同目录 g0-tests.xml。警告来自当前 Starlette 测试客户端对 httpx 的弃用提示，不影响本次断言；锁定依赖下可复现，后续升级测试客户端时处理。

真实 HTTP 冒烟：`python -s -m infra.smoke` 通过，直接连接 loopback，不使用系统 HTTP 代理。检查了 live、ready、OpenAPI、未登录拒绝、两租户登录与供应商数量，以及跨租户酒店读取 404。

其他检查：`pip check` 无破损依赖；Ruff check 与 format check 通过。迁移在空数据库上执行成功，重跑迁移与种子测试通过。

## G0 验收规格映射

| 计划编号 | 实测证据 | 结论 |
| --- | --- | --- |
| G0-01 环境 | test_environment 验证 prefix、Python 版本和禁用用户 site | 通过 |
| G0-02 迁移 | 首次空库迁移成功；test_migrate_and_seed_repeat 重跑 | 通过 |
| G0-03 种子 | 两次 seed 后租户供应商数仍为 A=2/B=1 | 通过 |
| G0-04 认证 | 缺令牌、损坏、过期、错误签名、未知成员负测 | 通过 |
| G0-05 API 隔离 | 两租户列表与跨租户 UUID 读取 | 通过 |
| G0-06 membership | 非成员租户、禁用成员、禁用租户均拒绝 | 通过 |
| G0-07 RLS 读取 | 应用角色直接查询；无上下文时看不到业务数据和成员关系 | 通过 |
| G0-08 RLS 写入 | 跨租户 INSERT 拒绝；UPDATE/DELETE 不影响目标记录 | 通过 |
| G0-09 外键 | 跨租户酒店引用失败；跨租户/同租户错误版本指针失败 | 通过 |
| G0-10 连接池 | 两租户交替访问；异常回滚后及无上下文请求无残留 | 通过 |
| G0-11 角色 | 权限守卫拒绝 auditor 管理操作；当前业务 API 只读 | 通过 |
| G0-12 文件 | F00 通过、F01～F05 命中预期错误码 | 通过 |
| G0-13 口径 | 前导零、三晚区间、Decimal/非法金额、未知与零 | 通过 |
| G0-14 库存池 | 两计划同池四间；不同池拒绝直接合计 | 通过 |
| G0-15 就绪/错误 | DB 不可用、迁移落后时 503；live 不受影响；错误含 trace_id | 通过 |
| G0-16 可复现入口 | 项目脚本检查与契约校验；新进程启动 API；真实 HTTP 验证 | 通过 |

## 实施中解决的问题

1. Docker 引擎不可用，改用专属 Conda 环境内的本地 PostgreSQL；没有以 SQLite 或 mock 替代隔离验收。
2. 受限沙箱不允许本机数据库 TCP 访问；真实 DB 和 HTTP 测试在允许本机访问的执行上下文完成。
3. 系统环境存在 POSTGRES_PASSWORD，曾影响 initdb 口令。已同步项目实例，并改为 HOTEL_POSTGRES_PASSWORD。
4. Windows 用户级 site-packages 会混入无关包；入口统一添加 `-s`，锁文件重新生成，新增隔离断言后全部重测通过。
5. 系统 HTTP 代理干扰 loopback 请求；冒烟入口显式 trust_env=False。

## 已知边界及 G1 输入

- 开发登录是本地演示适配器，没有生产 IdP、用户管理或完整登录防护。
- 版本及审计的不可变性针对应用角色；迁移/维护角色仍是受信任的管理边界。
- 当前 CSV 校验是受控完整包；未实现上传大小限制、压缩防护、部分 delta 解析、XLSX/JSON、激活、并发恢复。
- 行定位为解析记录序号。样例包含冻结业务时钟，但运行认证使用真实时钟。
- 双供应商语义由供应商种子和别名规范承接；尚无有效映射事实或自动合并结果，待 G1 建立来源实体与人工映射。
- 审计当前为基础表与种子事件，完整业务变更链在各业务命令落地时扩展。
- 未做新机器从零下载依赖的外部复现、容量压测、备份恢复演练或真实渠道出站验证，不将其计为 G0 成果。

G1 可直接使用 F00、Schema v1、身份与事务组件、基础迁移和隔离回归测试，继续完成 CSV 原件到单渠道模拟器的纵向闭环。完整范围见根目录实施计划第 10 节。
