# G4 模型不可用及评估

先查看模型是否已准备、是否启用，再看建议的实际attempt记录。prepared/enabled只是配置状态，不是成功推理证明。缺权重、hash不符、进程退出、超时均显示RULE_FALLBACK；人工可以继续现有映射流程。

重新准备权重运行`g4.ps1 prepare-model`；不要在页面填任意模型URL，也不要启用remote code。权重位于.runtime/models/multilingual-minilm，manifest记录固定revision、许可证和本地文件hash。外部推理未实现，不会把来源传给外部模型服务。

超时会结束推理子进程；Worker保留错误、owner/fence和历史，不覆盖人工业务事实。相同有效输入复用缓存；失败回退保留并明确展示。原因消除后可在页面“重开有限建议预算”，或POST建议retry命令，携带当前fence、action=retry及reason；最多3轮，不能无限重试。新来源/候选/规则/模型版本是新输入。

人工拒绝候选配对形成追加反馈；相同来源事实/目标版本不重复推荐。事实或目标变化后可以重新评估，旧拒绝不成为永久酒店黑名单。

评估运行`g4.ps1 eval`：固定datasets/g4/matching-frozen.json，不读开发库作为金标准；输出g4-ai-eval.json/.md及g4-model-runs.jsonl。dev用于阈值，test单列MATCH/NONE和置信区间。当前test实际模型Top1优于规则，但NONE误建议更多；不可宣传自动匹配准确率或把高分当作人工批准。

性能仅模型子进程：冷加载首请求单列，热推理199样本，线程2；RSS不含Postgres/API。完整业务链用g4-http-smoke证据，G3一万格协议结果不代表G4吞吐。
