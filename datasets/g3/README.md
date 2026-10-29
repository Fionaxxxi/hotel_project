# G3 可复现合成样例

所有酒店、来源、账号与故障均虚构，沿用datasets已有数据来源与许可说明，不是AI评估集。

`compatible/source/`只含可上传的v2 manifest.json与data.json；在G2页只选择这两个文件。各目录scenario.json是演示期望和参数，**不是上传文件**。默认明确选择NONREF、buffer1、SIM-A2/SIM-B1；输入仍保留FLEX以演示能力阻断。

`python -s -m infra.generate_g3`重建相同逻辑的静态演示包，不修改数据库。固定来源G3-DEMO seq1首次激活后重复上传会复用，不能以改key强制回退来源。

排序/故障/并发用例由tests/test_g3_*.py为独立资源生成，避免复用演示版本污染结论。故障只在精确G3测试库开启。规模数据由infra.g3_benchmark生成唯一pool/day、plan/day，实际数量/请求数/观察结果写入报告，不用重复键凑规模。

`g3.ps1 demo`是独立正常服务的真实HTTP闭环，会在开发库追加随机虚构来源，完成双渠道核验及管理员停卖，不注入故障，不删除历史数据。
