param([ValidateSet('prepare','setup','start','stop','status','test','fault-test','benchmark')][string]$Action='start')
$ErrorActionPreference='Stop'
$ProjectRoot=(Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ProjectPython='D:\miniconda3\envs\hotel_project\python.exe'
Set-Location -LiteralPath $ProjectRoot
function Invoke-Python {
 & $ProjectPython -s @args
 if($LASTEXITCODE -ne 0){throw "Project command failed ($LASTEXITCODE)"}
}
function Stop-Worker([int]$Index){
 $marker=Join-Path $ProjectRoot ".runtime\g2-worker-$Index.pid"
 if(Test-Path -LiteralPath $marker){
  $workerId=[int](Get-Content -LiteralPath $marker)
  $details=Get-CimInstance Win32_Process -Filter "ProcessId=$workerId"
  if($details){
   if($details.ExecutablePath -ne $ProjectPython -or -not $details.CommandLine.Contains('services.core.g2.worker')){throw "PID $workerId is not a project G2 Worker"}
   Stop-Process -Id $workerId
  }
  Remove-Item -LiteralPath $marker
 }
}
function Start-Hidden([string]$Name,[string[]]$Arguments){
 $marker=Join-Path $ProjectRoot ".runtime\$Name.pid"
 if(Test-Path -LiteralPath $marker){throw "Existing $Name marker; stop verified project services first"}
 $service=Start-Process -FilePath $ProjectPython -ArgumentList (@('-s','-m')+$Arguments) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $ProjectRoot ".runtime\$Name.stdout.log") -RedirectStandardError (Join-Path $ProjectRoot ".runtime\$Name.stderr.log") -PassThru
 $service.Id | Set-Content -LiteralPath $marker
 Write-Output "$Name PID $($service.Id)"
}
function Start-Workers { $lanes=@('parse','control','publish');foreach($index in 0..2){Start-Hidden "g2-worker-$index" @('services.core.g2.worker','--lane',$lanes[$index])}}
switch($Action){
 {$_ -in 'prepare','setup'}{
  & .\infra\scripts\g1.ps1 setup
  Invoke-Python -m infra.generate_g2
 }
 'stop'{
  Stop-Worker 0; Stop-Worker 1; Stop-Worker 2
  & .\infra\scripts\g1.ps1 stop
 }
 'start'{
  if(Test-Path -LiteralPath '.runtime\dispatcher.pid'){throw 'Stop the G1 dispatcher before starting G2'}
  Invoke-Python -m infra.local_postgres start
  $savedFault=$env:SIM_FAULTS_ENABLED
  try{$env:SIM_FAULTS_ENABLED='false';Start-Hidden 'sim' @('uvicorn','services.channel_simulator.main:create_app','--factory','--host','127.0.0.1','--port','8001')}
  finally{$env:SIM_FAULTS_ENABLED=$savedFault}
  Invoke-Python -m infra.wait_services sim
  Start-Hidden 'api' @('uvicorn','services.core.main:create_app','--factory','--host','127.0.0.1','--port','8000')
  Invoke-Python -m infra.wait_services core
  Start-Workers
 }
 'status'{
  Invoke-Python -m infra.wait_services all
  foreach($index in 0..2){
   $marker=Join-Path $ProjectRoot ".runtime\g2-worker-$index.pid"
   if(-not(Test-Path -LiteralPath $marker)){throw "Missing G2 Worker $index"}
   $workerId=[int](Get-Content -LiteralPath $marker)
   $details=Get-CimInstance Win32_Process -Filter "ProcessId=$workerId"
   if(-not $details -or $details.ExecutablePath -ne $ProjectPython -or -not $details.CommandLine.Contains('services.core.g2.worker')){throw "G2 Worker $index unavailable"}
   Write-Output "G2 Worker $index ready: PID $workerId"
  }
 }
 {$_ -in 'test','fault-test'}{
  $resume=(Test-Path -LiteralPath '.runtime\g2-worker-0.pid') -or (Test-Path -LiteralPath '.runtime\g2-worker-1.pid') -or (Test-Path -LiteralPath '.runtime\g2-worker-2.pid')
  Stop-Worker 0; Stop-Worker 1; Stop-Worker 2
  $oldProfile=$env:HOTEL_TEST_PROFILE
  try{$env:HOTEL_TEST_PROFILE='g2'; & .\infra\scripts\g1.ps1 test}
  finally{$env:HOTEL_TEST_PROFILE=$oldProfile;if($resume){Start-Workers}}
 }
 'benchmark'{
  $oldProfile=$env:HOTEL_TEST_PROFILE
  try{$env:HOTEL_TEST_PROFILE='g2';Invoke-Python -m infra.prepare_tests;Invoke-Python -m infra.g2_benchmark}
  finally{$env:HOTEL_TEST_PROFILE=$oldProfile}
 }
}
