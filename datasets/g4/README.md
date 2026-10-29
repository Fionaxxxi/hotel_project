# G4 冻结合成评估与场景来源

matching-frozen.json：200 query / 50物业组；35组MATCH（140条）、15组NONE（60条）。每组4个派生query，按组奇偶切分dev/test，同组不跨切分。目标名称是虚构模板，无真实酒店数据或独立双人金标准。

生成器和评估入口：infra/g4_eval.py。再次生成若内容不同会拒绝覆盖，避免测试集静默变更；正式新版本应另建数据文件。阈值仅根据dev的NONE校准，test原始预测单独保存。候选预构建，包内覆盖率不是数据库召回指标。

价格/库存/范围/模型停用/策略暂停等场景由tests/test_g4*.py与infra/g4_smoke.py使用tests/g2_helpers.files生成：保留虚构来源、显式sequence、完整scope和每次期望。纯规则阈值预期手写，真实故障退出程序只允许精确G4测试库。

hard-conflicts-v1.json为新增60组互不重复的冻结合成冲突配对：地域、床型、人数、父级、餐食、付款、税费及取消政策/非法政策；人工编写预期，独立于匹配实现。g4-hard-conflicts.xml记录60项通过，高模型分不能覆盖硬冲突。历史重复断言仍保留，不冒充独立样本；新增数据也不声称真实酒店双人标注。

原始结果位于docs/acceptance/g4-model-runs.jsonl，指标、置信区间和资源口径位于g4-ai-eval.json/.md；本地限定范围全项结论及持续限制见g4-report.md。
