param()
$ErrorActionPreference='Stop'
$ProjectRoot=(Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ProjectPython='D:\miniconda3\envs\hotel_project\python.exe'
Set-Location -LiteralPath $ProjectRoot
function Invoke-FinalPython {
 & $ProjectPython -s @args
 if($LASTEXITCODE -ne 0){throw "Final acceptance command failed ($LASTEXITCODE)"}
}
# Run only after the complete regression and its normal-service restoration.
Invoke-FinalPython -m infra.g5_final_health
Copy-Item -LiteralPath 'docs/acceptance/g5-http-smoke.json' -Destination 'docs/acceptance/g5-before-pool-recovery-http-smoke.json'
Invoke-FinalPython -m infra.g5_demo
Invoke-FinalPython -m infra.g5_final_health
Invoke-FinalPython -m infra.g5_secret_scan
# A preflight archive exists; final archive is validated again after report generation.
Invoke-FinalPython -m infra.g5_evidence
Invoke-FinalPython -m infra.g5_scenario_matrix
Invoke-FinalPython -m infra.g5_evidence
$summary=Get-Content -Raw -LiteralPath 'docs/acceptance/g5-acceptance-summary.json' | ConvertFrom-Json
if($summary.open_local_items.Count -ne 0){throw 'Local acceptance still has open items; do not sign or package as complete'}
$planPath=Join-Path $ProjectRoot 'G5阶段详细实施计划.md'
$planText=[System.IO.File]::ReadAllText($planPath,[System.Text.Encoding]::UTF8)
$planText=[regex]::Replace($planText,'(?m)^- \[ \] (M[0-6]：)','- [x] $1')
[System.IO.File]::WriteAllText($planPath,$planText,[System.Text.UTF8Encoding]::new($false))
Invoke-FinalPython -m infra.g5_release
