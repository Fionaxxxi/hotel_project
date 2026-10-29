# Recovery v1

备份schema `g5-backup-v1`：COMPLETE标记、三库inventory、各文件字节数/hash、schema、迁移checksum、源码hash、时间和scope。没有.env/秘密。只允许项目loopback实例和新恢复库前缀，不允许覆盖开发库。

恢复schema `g5-restore-v1`：新目标、逐表内容对照、新原件根、实际耗时、默认禁止派发。校验backup后进行的显式前向迁移必须单独说明，不将升级后的全部字节仍称原备份字节一致。

旧来源在 `g5_recovery_scope` 中HELD。默认只读；本机可信CLI完成隔离及远端查询后，只发放“新独立来源可运行”的本地实例许可。旧epoch/旧批准/旧外盒不能自动恢复售卖。

`GET /v5/recovery/scopes`受租户限制。管理员可用 `POST /v5/recovery/listings/{id}/stop` 提交expected_version/reason，必须真实完整读取远端且日期格集合一致；记录counter floor后只建立更高revision的STOP。未知/范围变化阻断。

`POST /v5/recovery/release`要求source_key/reason，实际验证恢复后新STOP、远端内容/revision、全部关闭及本地CAS；只解除恢复HELD，保留新审批和reopen所需的G3 stop fence。

隔离复现固定端口8005/8006/8007，模拟器配置必须与恢复库前缀同时使用。普通核心拒绝该配置；不接受任意适配器URL或重定向目的地。
