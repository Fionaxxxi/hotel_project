# SIM-A G3 协议

保留/v1旧整包协议，G3使用/g3。正常入口127.0.0.1:8001。仅固定服务器注册表可选地址，不跟随重定向或接受用户URL。

PUT /g3/listings/{source_listing_key}，Idempotency-Key必需。JSON字段严格为revision、action、manifest_hash、chunk_index、content、manifest、cells。CONTENT为chunk0，提交content与manifest且cells空；PUBLISH/REPAIR/STOP块content/manifest为null，按manifest提交cells。每块最多30日期格。

CONTENT存储最高接纳revision、wire content与chunk manifest；后续同版本不同chunk允许，同版本不同manifest或chunk hash不符拒绝。低revision实际迟到执行被拒，历史同key仍可查回执但不能再次写当前状态。一个chunk同步原子应用，不声称跨chunk原子。

规范化content包含hotel_key、scope、pools、plans；每个plan保留渠道编码、房型、池、USD/两成人/餐食付款税费与FLEX/NONREF明确小时政策。G3不支持任意取消阶梯。cell为pool数量或rate净价/售价，含ISO住宿日与sellable。STOP只允许关闭单元，不能夹带开售。

GET /g3/operations/{request_key}返回operation_id、state、result；GET /g3/listings/{key}返回最高revision、wire content、逐cell实际值与应用revision、complete标识。对账须规范化且逐格比较。新业务账号独立，跨账号资源404。

核心账号写许可5次/秒，模拟器DB令牌桶也独立执行5次/秒（允许最多5次突发）；两个限制不是同一处内存计数。核心读取预算单独3次/秒。正常故障接口关闭。
