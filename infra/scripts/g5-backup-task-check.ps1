$ErrorActionPreference='Stop'
$ProjectRoot=(Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$TaskName='HotelProject-G5-DailyBackup'
& (Join-Path $PSScriptRoot 'g5-backup-task.ps1') status
$Task=Get-ScheduledTask -TaskName $TaskName
if($Task.State -eq 'Running'){throw 'The project backup task is already running'}
$Started=Get-Date
Start-ScheduledTask -TaskName $TaskName
$Deadline=$Started.AddMinutes(3)
do{
 Start-Sleep -Seconds 1
 $Info=Get-ScheduledTaskInfo -TaskName $TaskName
 $Task=Get-ScheduledTask -TaskName $TaskName
 if($Info.LastRunTime -ge $Started.AddSeconds(-2) -and $Task.State -ne 'Running'){break}
}while((Get-Date) -lt $Deadline)
$Finished=Get-Date
$Report=@{
 installed=$true
 started_at=$Started.ToUniversalTime().ToString('o')
 finished_at=$Finished.ToUniversalTime().ToString('o')
 task_name=$TaskName
 last_run_at=$Info.LastRunTime.ToUniversalTime().ToString('o')
 next_run_at=$Info.NextRunTime.ToUniversalTime().ToString('o')
 manual_action_exit_code=$Info.LastTaskResult
 state=[string]$Task.State
 timezone=(Get-TimeZone).Id
 scope='Actual Windows scheduled task action, current interactive user, local project services only'
}
$Report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ProjectRoot 'docs\acceptance\g5-backup-schedule.json')
if($Info.LastRunTime -lt $Started.AddSeconds(-2) -or $Task.State -eq 'Running' -or $Info.LastTaskResult -ne 0){throw 'Actual scheduled backup action did not finish successfully'}
Write-Output 'Actual Windows scheduled backup action completed with exit code 0'
