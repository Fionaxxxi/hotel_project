# G4 全项验收证据矩阵

50项按原计划编号逐项归档。PASS仅指本地收敛范围；模型效果、真实性与容量限制见每项说明及正式报告。完整回归201项和独立60组难例均通过，二者是两个真实执行的套件。

| 编号 | 场景 | 结论 | 证据 | 范围说明 |
| --- | --- | --- | --- | --- |
| G4-01 | G3兼容基线复跑 | 通过 | [g4-baseline.xml](g4-baseline.xml)、[g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-02 | 实际本地模型推理 | 通过 | [g4-model-runs.jsonl](g4-model-runs.jsonl)、[g4-final-browser-server.json](g4-final-browser-server.json) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-03 | 结构化字段与模型职责 | 通过 | [README.md](../../contracts/ai/v1/README.md)、[g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-04 | 冻结候选白名单 | 通过 | [g4-tests.xml](g4-tests.xml)、[ai.py](../../services/core/g4/ai.py) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-05 | 引用及原文核验 | 通过 | [g4-browser.json](g4-browser.json)、[g4-final-browser-server.json](g4-final-browser-server.json) | 建议引用为冻结事实及投影位置；原件定位继续沿既有provenance，不把投影行号冒充原始行号。 |
| G4-06 | 非法Schema/数值/超大输出 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-07 | 同名异店与NONE | 通过 | [g4-ai-eval.json](g4-ai-eval.json)、[g4-browser.json](g4-browser.json) | NONE误建议如实单列；人工可不采用任何建议，不启用自动匹配。 |
| G4-08 | 房型/政策硬冲突 | 通过 | [g4-hard-conflicts.xml](g4-hard-conflicts.xml)、[hard-conflicts-v1.json](../../datasets/g4/hard-conflicts-v1.json) | 60组语义输入互不重复；合成、无独立双人标注，不外推真实分布。 |
| G4-09 | 输入提示注入/HTML | 通过 | [g4-tests.xml](g4-tests.xml)、[g4-browser.json](g4-browser.json)、[g4-final-narrow-security.png](g4-final-narrow-security.png) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-10 | 缓存隔离和版本失效 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-11 | 缺模型/断网/停用 | 通过 | [g4-http-smoke-disabled.json](g4-http-smoke-disabled.json) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-12 | 真正超时与预算 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-13 | AI Worker崩溃与旧结果 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-14 | 模型无写权限 | 通过 | [local_model.py](../../services/core/g4/local_model.py)、[model_process.py](../../services/core/g4/model_process.py)、[g4-final-browser-server.json](g4-final-browser-server.json) | 推理进程不传DB/渠道凭证，不加载任意远程代码；不是操作系统恶意代码隔离证明。 |
| G4-15 | 策略默认关闭与跨租户 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-16 | 策略四眼审批 | 通过 | [g4-tests.xml](g4-tests.xml)、[g4-browser.json](g4-browser.json) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-17 | 策略范围与重叠 | 通过 | [g4-tests.xml](g4-tests.xml)、[011_g4_automation.sql](../../infra/migrations/011_g4_automation.sql)、[policy.py](../../services/core/g4/policy.py) | 同source仅一条ENABLED策略由数据库唯一索引和状态命令共同约束；范围逐字段比较。 |
| G4-18 | 两类授权分离 | 通过 | [g4-tests.xml](g4-tests.xml)、[g4-final-browser-server.json](g4-final-browser-server.json) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-19 | 常规小幅价格更新 | 通过 | [g4-final-browser-server.json](g4-final-browser-server.json)、[g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-20 | 阈值端点及Decimal | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-21 | 连续小涨累计越界 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-22 | 条款/币种/规则变化 | 通过 | [g4-tests.xml](g4-tests.xml)、[g4-hard-conflicts.xml](g4-hard-conflicts.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-23 | 安全库存更新 | 通过 | [g4-tests.xml](g4-tests.xml)、[g4-final-browser-server.json](g4-final-browser-server.json) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-24 | 容量不足或库存骤降 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-25 | 范围/删除与文件mode | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-26 | 无来源序号/模板未知 | 通过 | [g4-tests.xml](g4-tests.xml)、[workflow.py](../../services/core/g4/workflow.py) | 当前合同要求source_sequence，旧序号/未知合同不获得自动激活资格。 |
| G4-27 | 每快照独立机器决定 | 通过 | [g4-final-browser-server.json](g4-final-browser-server.json)、[g4-browser.json](g4-browser.json) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-28 | 旧人审接口不能伪机器批准 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-29 | 决策到提交之间撤权 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-30 | 派发许可前后暂停竞争 | 通过 | [g4-tests.xml](g4-tests.xml)、[g4-race-tests.xml](g4-race-tests.xml) | 测试延迟发生在派发许可提交后、实际HTTP前；暂停后放行真实请求仍可落地，不称为撤回。 |
| G4-31 | 停用不等于停售 | 通过 | [g4-http-smoke.json](g4-http-smoke.json)、[g4-browser.json](g4-browser.json) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-32 | 停售防复活 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-33 | 工作流所有权 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-34 | 合格更新无逐步点击 | 通过 | [g4-final-browser-server.json](g4-final-browser-server.json)、[g4-final-desktop-workflow.png](g4-final-desktop-workflow.png) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-35 | 映射安全复用 | 通过 | [g4-tests.xml](g4-tests.xml)、[g4-final-browser-server.json](g4-final-browser-server.json) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-36 | 新实体等待与恢复 | 通过 | [g4-tests.xml](g4-tests.xml)、[g4-browser.json](g4-browser.json) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-37 | 策略外异常人工批准 | 通过 | [g4-final-browser-server.json](g4-final-browser-server.json)、[g4-final-narrow-wait.png](g4-final-narrow-wait.png) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-38 | 质量/范围人工处理 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-39 | 额度修正恢复 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-40 | 新来源并发/迟到事件 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-41 | 重复消费与并发Worker | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-42 | 真进程崩溃窗口 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-43 | G3故障兼容 | 通过 | [g4-tests.xml](g4-tests.xml) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-44 | AI高负载下关闭 | 通过 | [g4-http-smoke.json](g4-http-smoke.json) | 135次实际推理与关闭并行；仅本次CPU负载，不冒称100个生产队列积压或SLA。 |
| G4-45 | RLS/FK与资源鉴权 | 通过 | [g4-tests.xml](g4-tests.xml)、[g4-environment.json](g4-environment.json) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
| G4-46 | 真实浏览器闭环 | 通过 | [g4-browser.json](g4-browser.json)、[g4-final-narrow-stop.png](g4-final-narrow-stop.png)、[g4-final-desktop-stop.png](g4-final-desktop-stop.png) | 浏览器viewport 390×844与1280×800；排除滚动条后页面宽375/1265，无页面横向溢出，四列表局部滚动。 |
| G4-47 | 规则与实际模型对照 | 通过 | [g4-ai-eval.json](g4-ai-eval.json)、[g4-model-runs.jsonl](g4-model-runs.jsonl) | 候选为预构建包，召回只报告包内覆盖；并非数据库召回/生产准确率验收。 |
| G4-48 | 实际性能与预算 | 通过 | [g4-ai-eval.json](g4-ai-eval.json)、[g4-http-smoke.json](g4-http-smoke.json) | 冷热分开199热样本，RSS为模型子进程；不含API/数据库。 |
| G4-49 | 全链追溯与安全展示 | 通过 | [g4-final-browser-server.json](g4-final-browser-server.json)、[g4-environment.json](g4-environment.json) | 原件→G2/G3 provenance和新增引用可追溯；完整一键审计回放体验属于G5。 |
| G4-50 | 独立进程最终演示与交接 | 通过 | [g4-http-smoke.json](g4-http-smoke.json)、[g4-http-smoke-disabled.json](g4-http-smoke-disabled.json)、[g4-final-browser-server.json](g4-final-browser-server.json)、[g4-local.md](../runbooks/g4-local.md) | 当前本机合成来源及独立模拟渠道范围；详见正式报告与原始测试。 |
