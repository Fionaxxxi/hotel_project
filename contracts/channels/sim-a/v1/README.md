# SIM-A v1 渠道契约

固定本机HTTP地址127.0.0.1:8001。Authorization Bearer令牌映射到A/B模拟账号，不能用请求体任意选择账号。独立模拟库中的账号RLS适用于listing/operation。

PUT /v1/listings/{external_hotel_code}：请求头Idempotency-Key；请求体恰好包含revision（正整数）、payload_hash（SHA-256）、payload。payload包含capability=sim-a-v1、hotel_key、scope、pools、plans。hotel_key须与路径一致。

pools：[key, calendar]，calendar每个元素为date/quantity，quantity为非负整数。plans元素包含key、room_key、pool_key、adults=2、currency=USD、meal、payment、tax_basis、cancellation、calendar。多个计划可引用同一pool_key，库存只写一次。calendar的date覆盖scope的全部住宿日，净价为四位字符串，售价为两位字符串。支持FLEX/NONREF及明确取消小时数和stay_total基数；不接受UNKNOWN条款。

一次listing bundle覆盖酒店、相关计划和共享库存池，独立数据库事务内完成revision比较、listing落地及operation保存。相同幂等键/请求hash返回原operation；相同key不同内容409。旧revision或同revision不同业务hash也409。无效金额、日期、共享池引用或不支持的字段422。

GET /v1/operations/{idempotency_key}返回当前账号的操作回执；GET /v1/listings/{external_hotel_code}返回revision、payload_hash和远端payload。其他账号资源返回404，认证不通过401。回执含id、account_id、idempotency_key、request_hash、listing_key、revision及payload_hash。

核心校验两个查询均成功，回执与远端revision/hash一致，且对远端payload重新计算hash一致，才记VERIFIED。200受理不等于核验完成。

开发故障：SIM_FAULTS_ENABLED=true且请求X-Sim-Delay=true时，先提交远端事务，再延迟2秒响应。正常启动明确关闭此模式。测试客户端0.3秒超时后应查询原operation，不能新建key盲重发。
