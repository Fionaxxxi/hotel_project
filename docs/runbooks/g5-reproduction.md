# G5 交付、离线准备与复现

当前唯一项目环境为 `D:\miniconda3\envs\hotel_project`。新Windows主机需先安装D盘Miniconda和Node；现有证据环境版本写入验收环境文件。不要在base安装项目依赖。

```powershell
cd D:\hotel
# 全新环境：只在该目标不存在时创建
& 'D:\miniconda3\Scripts\conda.exe' create --prefix 'D:\miniconda3\envs\hotel_project' --file conda-explicit-win-64.txt
& 'D:\miniconda3\envs\hotel_project\python.exe' -s -m pip install -r requirements.lock.txt
& 'D:\miniconda3\envs\hotel_project\python.exe' -s -m infra.generate_assets
& 'D:\miniconda3\envs\hotel_project\python.exe' -s -m infra.local_postgres start
& 'D:\miniconda3\envs\hotel_project\python.exe' -s -m infra.manage provision
& 'D:\miniconda3\envs\hotel_project\python.exe' -s -m infra.manage migrate
& 'D:\miniconda3\envs\hotel_project\python.exe' -s -m infra.manage seed
& 'D:\miniconda3\envs\hotel_project\python.exe' -s -m infra.g1_setup
npm ci --prefix apps/admin
.\infra\scripts\g5.ps1 setup
.\infra\scripts\g5.ps1 prepare-model
.\infra\scripts\g5.ps1 start
.\infra\scripts\g5.ps1 status
```

准备依赖/公开模型可以联网，正常启动和业务运行不静默下载。准备完成后以G5离线保护运行；本机固定模型缓存可校验。不要把“缓存齐全后可离线运行”说成“没有包缓存也能完全离线安装”。新机器需独立生成.env秘密，不能复制公开包中不存在的本机凭证。

## 本机隔离复现

```powershell
& 'D:\miniconda3\envs\hotel_project\python.exe' -s -m infra.g5_reproduction --backup 'D:\hotel\.runtime\g5-backups\<当前兼容备份ID>'
```

工具新建两个源码目录和两组三库，不重置任何旧目标；在各目录执行独立的 `npm ci --offline` 和构建，使用同一个专属Conda环境及已准备模型。复现API8005，两个独立模拟器8006/8007，范围配置必须与恢复库前缀同时匹配，不能把这个配置用于普通核心库。端口被占用时应检查失败，不终止未知进程。

演示使用固定自然业务编码 `G5-DEMO-001`、固定合成字段和业务时钟；技术UUID/trace ID正常生成。比较两次实际业务payload hash和双关闭结果，不能要求技术主键相同。复现的业务写入独立模拟库，finally关闭它启动的全部服务；普通8000/8001/8002实例不重置。

这只能证明同一机器上的源码、前端构建、隔离数据库及缓存模型复现。用户已确认暂无另一Windows环境，因此独立新机器项继续待验，不将本机新目录、恢复新库或共享已有Conda环境冒称干净Windows安装验收。

## 交付包

```powershell
.\infra\scripts\g5.ps1 export-contract
.\infra\scripts\g5.ps1 release
```

生成本地ZIP与逐文件manifest。包含源码、锁文件、契约、文档和固定公开模型缓存；排除.env、运行数据库、原件、备份、日志、node_modules及缓存。逐项检查当前实际秘密值、校验压缩包和文件SHA-256。hash只能检测内容一致性，不是外部数字签名。默认不发布到任何外部服务。

`docs/acceptance/g5-release.json`是ZIP外部回执，记录最终包路径、大小和SHA-256，不放入包内，避免包包含自身最终hash的循环引用。包内`handoff-manifest.json`记录每个实际文件的hash；验收摘要也不把外部回执加入内容hash集合。录像按用户明确决定列为待补，截图与浏览器操作不作为录像替代。

新机器复现时保留OS、CPU、Node/Conda/Python/PostgreSQL版本、命令退出码、测试原始报告和两类恢复结果。遇到缺失包、AVX2或系统权限差异应记录真实失败，不改用base或假模型绕过。
