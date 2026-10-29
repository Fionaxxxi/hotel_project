# Audit v1

`GET /v5/audit/{listing|snapshot|run|batch}/{uuid}` 是受权只读图。返回schema `g5-audit-v1`、root、nodes、edges、missing、complete、truncated和预算。所有节点通过当前tenant事务/RLS读取。body中的tenant不能替换调用身份。

`POST .../export` 仅auditor/tenant_admin，返回 `g5-export-v1` manifest与evidence并新增一条导出审计。manifest的SHA-256基于UTF-8、sort_keys、紧凑分隔、ensure_ascii=false、禁止NaN的JSON字节。`services.core.g5.audit.verify_package`可离线复算。5MiB导出限制；截断图的complete保持false。

原文当作数据，不能解释为HTML、系统指令或远程URL。storage_key和敏感键脱敏；原件下载沿既有鉴权接口。历史缺失不补造引用，staging逻辑记录不代表原件物理行。
