param([ValidateSet('install','status','uninstall')][string]$Action='status')
$ErrorActionPreference='Stop'
$ProjectRoot=(Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$TaskName='HotelProject-G5-DailyBackup'
$ScriptPath=Join-Path $PSScriptRoot 'g5.ps1'
$Existing=Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if($Existing -and -not ($Existing.Actions.Arguments -contains ('-NoProfile -NonInteractive -WindowStyle Hidden -File "'+$ScriptPath+'" backup'))){throw 'Existing task is not the verified project backup task'}
if($Action -eq 'install'){
 $PowerShellBinary=(Get-Process -Id $PID).Path
 $RunAction=New-ScheduledTaskAction -Execute $PowerShellBinary -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -File "'+$ScriptPath+'" backup') -WorkingDirectory $ProjectRoot
 $Trigger=New-ScheduledTaskTrigger -Daily -At '02:00'
 $TaskSettings=New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
 $UserName=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name
 $Principal=New-ScheduledTaskPrincipal -UserId $UserName -LogonType Interactive -RunLevel Limited
 Register-ScheduledTask -TaskName $TaskName -Action $RunAction -Trigger $Trigger -Settings $TaskSettings -Principal $Principal -Description 'Local hotel project consistent three-store backup; only while user session is available; never deletes history' -Force | Select-Object TaskName,State
}elseif($Action -eq 'uninstall'){
 if($Existing){Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false}
}else{
 if(-not $Existing){Write-Output 'Project daily backup task is not installed';exit 1}
 $Existing | Select-Object TaskName,State
 Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object LastRunTime,LastTaskResult,NextRunTime
 Get-TimeZone | Select-Object Id
}
