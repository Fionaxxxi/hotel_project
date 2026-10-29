# Run only after the isolated full suite has finished. Never parallelize heavy benchmarks.
$ErrorActionPreference='Stop'
$ProjectRoot=(Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ProjectPython='D:\miniconda3\envs\hotel_project\python.exe'
Set-Location -LiteralPath $ProjectRoot
function Run-Project {
 & $ProjectPython -s @args
 if($LASTEXITCODE -ne 0){throw 'Acceptance command failed; inspect the preceding bounded output'}
}
& .\infra\scripts\g5.ps1 stop
& .\infra\scripts\g5.ps1 start
Run-Project @('-m','infra.g5_close_initial_load','--evidence','g5-load-recon-backlog.json','g5-soak-recon-backlog.json','g5-load-inbox-race.json','--output','g5-recon-backlog-close.json')
Run-Project @('-m','infra.g5_load')
Run-Project @('-m','infra.g5_backup_failure_drill')
Run-Project @('-m','infra.g5_recovery_acceptance','prepare')
& .\infra\scripts\g5-backup-task-check.ps1
$Newest=Get-ChildItem -LiteralPath '.runtime\g5-backups' -Directory | Sort-Object LastWriteTime -Descending | Where-Object {Test-Path -LiteralPath (Join-Path $_.FullName 'manifest.json')} | Select-Object -First 1
if(-not $Newest){throw 'No complete backup after the actual task action'}
$Newest.FullName | Set-Content -LiteralPath '.runtime\g5-current-backup.txt'
Run-Project @('-m','infra.g5_recovery_acceptance','drills','--backup',$Newest.FullName)
Run-Project @('-m','infra.g5_reproduction','--backup',$Newest.FullName)
Run-Project @('-m','infra.g5_locator_evidence')
Run-Project @('-m','infra.g5_duplicate_delivery')
Run-Project @('-m','infra.g5_demo')
Run-Project @('-m','infra.g5_audit_sampling')
Run-Project @('-m','infra.g5_benchmark')
Run-Project @('-m','infra.g5_trace_evidence')
Run-Project @('-m','infra.g5_browser_fixture','prepare')
Run-Project @('-m','infra.g5_soak','--seconds','3600')
