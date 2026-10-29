# G5 全项场景证据矩阵

状态：PASSED_LOCAL_SCOPE_DEFERRED_ITEMS。PENDING不算通过；47独立Windows、49录像按用户决定待补，未签原计划全项退出。

| ID | 优先级 | 场景 | 状态 | 证据 / 说明 |
| --- | --- | --- | --- | --- |
| 01 | P0 | 重新执行G0～G4当前完整套件，统计实际收集数与全部结果 | PASS | [g5-baseline.xml](g5-baseline.xml) |
| 02 | P0 | 新增迁移不改写旧checksum；升级后旧业务数据与RLS保留 | PASS | [g5-setup-idempotence.json](g5-setup-idempotence.json)、[g5-tests.xml](g5-tests.xml) |
| 03 | P0 | 全端点角色矩阵；只读审计所有写动作拒绝 | PASS | [g5-security-matrix.json](g5-security-matrix.json)、[g5-tests.xml](g5-tests.xml) |
| 04 | P0 | 两租户互换对象/任务/原件/建议/策略ID，无越权读取写入 | PASS | [g5-tests.xml](g5-tests.xml) |
| 05 | P0 | 连接池复用、后台任务、缓存、分页/筛选均无租户串漏 | PASS | [g5-tests.xml](g5-tests.xml) |
| 06 | P0 | 旧/畸形JWT、成员撤销、开发登录关闭与失败限速有效 | PASS | [g5-tests.xml](g5-tests.xml) |
| 07 | P0 | 独立审批不可自批；客户端不能伪造机器actor或恢复许可 | PASS | [g5-tests.xml](g5-tests.xml) |
| 08 | P0 | 路径穿越、伪装格式、畸形/超限XLSX和长字段受控拒绝 | PASS | [g5-tests.xml](g5-tests.xml) |
| 09 | P0 | 来源XSS/提示指令仅字面显示，无映射/发布副作用 | PASS | [g5-browser.json](g5-browser.json)、[g5-tests.xml](g5-tests.xml) |
| 10 | P0 | CSV公式前缀含空白变体安全导出；原件hash不变 | PASS | [g5-tests.xml](g5-tests.xml) |
| 11 | P0 | 出站URL及重定向绕过被拒绝，不触达未授权地址 | PASS | [g5-tests.xml](g5-tests.xml) |
| 12 | P0 | 日志、错误、导出、交付包扫描无真实秘密 | PASS | [g5-secret-scan.json](g5-secret-scan.json) |
| 13 | P0 | HTTP→外盒→Worker→模拟器→回执关联可查询 | PASS | [g5-trace.json](g5-trace.json) |
| 14 | P0 | 队列数量/最老年龄与数据库事实对照；人工等待单列 | PASS | [g5-tests.xml](g5-tests.xml)、[g5-final-integration-supplement.xml](g5-final-integration-supplement.xml) |
| 15 | P0 | 外盒滞后/租约过期/死信告警发现与恢复；不伪成功 | PASS | [g5-tests.xml](g5-tests.xml) |
| 16 | P0 | UNKNOWN/429/来源过期/停卖待确认告警正确，确认不等于解除 | PASS | [g5-tests.xml](g5-tests.xml) |
| 17 | P0 | 指标租户隔离、标签有界、采集失败标记陈旧/UNKNOWN | PASS | [g5-tests.xml](g5-tests.xml) |
| 18 | P0 | 采集故障不阻断业务和紧急关闭 | PASS | [g5-tests.xml](g5-tests.xml)、[g5-final-integration-supplement.xml](g5-final-integration-supplement.xml) |
| 19 | P0 | 20条分层链全部回到版本、审批和实际观察 | PASS | [g5-audit-sampling.json](g5-audit-sampling.json) |
| 20 | P0 | CSV多行字段、XLSX sheet/row、JSON record/path定位准确 | PASS | [g5-original-locators.json](g5-original-locators.json) |
| 21 | P0 | G4投影记录和原件记录明确区分，引用缺失可见 | PASS | [g5-browser.json](g5-browser.json)、[g5-tests.xml](g5-tests.xml) |
| 22 | P0 | SYSTEM_POLICY、模型revision、人工反馈和epoch均可追溯 | PASS | [g5-audit-sampling.json](g5-audit-sampling.json)、[g5-http-smoke.json](g5-http-smoke.json) |
| 23 | P0 | 回放不重算/发任务；稳定分页和对象上限有效 | PASS | [g5-tests.xml](g5-tests.xml)、[g5-final-integration-supplement.xml](g5-final-integration-supplement.xml) |
| 24 | P0 | 导出跨租户/越权拒绝；脱敏包离线验证及篡改失败 | PASS | [g5-tests.xml](g5-tests.xml) |
| 25 | P0 | 备份维护屏障阻止新写/派发，未决在途有记录 | PASS | [g5-backup-failure-drill.json](g5-backup-failure-drill.json)、[g5-backup-schedule.json](g5-backup-schedule.json)、[g5-tests.xml](g5-tests.xml) |
| 26 | P0 | 联合备份完整；失败/磁盘不足包不可标完成，服务可恢复 | PASS | [g5-backup-failure-drill.json](g5-backup-failure-drill.json)、[g5-tests.xml](g5-tests.xml) |
| 27 | P0 | 恢复到新库新目录，拒绝开发库/错误路径/不完整包 | PASS | [g5-restore-summary.json](g5-restore-summary.json)、[g5-tests.xml](g5-tests.xml) |
| 28 | P0 | 核心+两模拟库联合恢复，hash/迁移/RLS一致 | PASS | [g5-restore-summary.json](g5-restore-summary.json) |
| 29 | P0 | 核心回退而远端已成功：查询原key，远端无重复效果 | PASS | [g5-restore-summary.json](g5-restore-summary.json)、[g5-tests.xml](g5-tests.xml) |
| 30 | P0 | 远端备份后已停卖：恢复旧目标不复活，旧epoch阻断 | PASS | [g5-restore-summary.json](g5-restore-summary.json)、[g5-tests.xml](g5-tests.xml) |
| 31 | P0 | 无法确定远端状态时保持UNKNOWN/冻结，未自动放行 | PASS | [g5-tests.xml](g5-tests.xml) |
| 32 | P0 | 两次恢复计时，RPO/RTO如实计算；每日调度/超期告警可验证 | PASS | [g5-backup-schedule.json](g5-backup-schedule.json)、[g5-restore-summary.json](g5-restore-summary.json) |
| 33 | P0 | 风险待办可跳正确处理入口，空/错/部分结果可理解 | PASS | [g5-browser.json](g5-browser.json) |
| 34 | P0 | 已批准策略换版重绑；旧决定失效，CAS冲突不误绑 | PASS | [g5-browser.json](g5-browser.json) |
| 35 | P0 | 桌面与390窄屏完成回放/导出/重绑/关闭，无整页横溢 | PASS | [g5-browser.json](g5-browser.json)、[g5-browser-desktop.png](g5-browser-desktop.png)、[g5-browser-narrow.png](g5-browser-narrow.png) |
| 36 | P0 | 关键流程键盘完成；焦点、标签、风险文字和401/409恢复有效 | PASS | [g5-browser.json](g5-browser.json) |
| 37 | P0 | 三格式1万/10万解析基准，解析与后续物化分开 | PASS | [g5-parse-benchmark.json](g5-parse-benchmark.json) |
| 38 | P0 | API/回放至少100样本、协议三档复测，保留失败与max | PASS | [g5-query-benchmark.json](g5-query-benchmark.json)、[g5-protocol-benchmark.json](g5-protocol-benchmark.json) |
| 39 | P0 | 十来源双渠道完整自动链五轮及1/2/4并发，统计全分母 | PASS | [g5-load.json](g5-load.json) |
| 40 | P0 | 实际模型任务竞争下10次关闭，确认远端状态并报告所有耗时 | PASS | [g5-load.json](g5-load.json) |
| 41 | P0 | 60分钟资源/队列稳定性，无不可解释增长或遗失任务 | PASS | [g5-soak.json](g5-soak.json)、[g5-soak-closure.json](g5-soak-closure.json) |
| 42 | P0 | 100次固定seed随机重复/乱序投递，远端无重复效果 | PASS | [g5-duplicate-delivery.json](g5-duplicate-delivery.json) |
| 43 | P0 | 最终完整回归、安全/契约/恢复均通过，正常故障入口关闭 | PASS | [g5-tests.xml](g5-tests.xml)、[g5-environment.json](g5-environment.json)、[g5-final-health.json](g5-final-health.json) |
| 44 | P0 | setup重复运行保留.env/数据；停止仅影响已核验项目进程 | PASS | [g5-setup-idempotence.json](g5-setup-idempotence.json)、[g5-tests.xml](g5-tests.xml) |
| 45 | P0 | 白名单隔离实例两次重建；范围错误拒绝，不清开发租户 | PASS | [g5-local-reproduction.json](g5-local-reproduction.json) |
| 46 | P0 | 准备后断网，人工/禁模型完整闭环；缓存模型真实推理可用 | PASS | [g5-local-reproduction.json](g5-local-reproduction.json)、[g5-tests.xml](g5-tests.xml) |
| 47 | P0 | 另一干净Windows实例按文档完成安装、测试、演示与恢复 | DEFERRED_BY_USER | 用户确认暂无独立 Windows 环境，先完成本机范围。 |
| 48 | P0 | 最终包无秘密，源码/依赖/模型/证据manifest可校验 | PASS | [g5-release.json](g5-release.json) |
| 49 | P1 | 12～15分钟与约2分钟真实录屏，标注版本和模拟边界 | DEFERRED_BY_USER | [g5-video.json](g5-video.json) |
| 50 | P0 | README/runbook/限制齐全；所有P0有证据，目标与实测分列 | PASS | [g5-report.md](g5-report.md)、[g5-acceptance-summary.json](g5-acceptance-summary.json) |
