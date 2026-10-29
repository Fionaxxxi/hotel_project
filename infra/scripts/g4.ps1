param([ValidateSet('prepare','setup','start','stop','status','test','contract-test','fault-test','demo','eval','prepare-model')][string]$Action='start')
$ErrorActionPreference='Stop'
$ProjectRoot=(Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ProjectPython='D:\miniconda3\envs\hotel_project\python.exe'
Set-Location -LiteralPath $ProjectRoot
function Invoke-Python { & $ProjectPython -s @args; if($LASTEXITCODE -ne 0){throw "Project command failed ($LASTEXITCODE)"} }
function Start-Local([string]$Name,[string[]]$Arguments){
 $marker=Join-Path $ProjectRoot ".runtime\$Name.pid"
 if(Test-Path -LiteralPath $marker){throw "Existing $Name marker; stop project services first"}
 $service=Start-Process -FilePath $ProjectPython -ArgumentList (@('-s','-m')+$Arguments) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $ProjectRoot ".runtime\$Name.stdout.log") -RedirectStandardError (Join-Path $ProjectRoot ".runtime\$Name.stderr.log") -PassThru
 $service.Id | Set-Content -LiteralPath $marker
 Write-Output "$Name PID $($service.Id)"
}
function Stop-Local([string]$Name,[string]$Module){
 $marker=Join-Path $ProjectRoot ".runtime\$Name.pid"
 if(Test-Path -LiteralPath $marker){
  $localProcessId=[int](Get-Content -LiteralPath $marker)
  $details=Get-CimInstance Win32_Process -Filter "ProcessId=$localProcessId"
  if($details){
   if($details.ExecutablePath -ne $ProjectPython -or -not $details.CommandLine.Contains($Module) -or -not $details.CommandLine.Contains('-s')){throw "Unverified process $localProcessId"}
   Stop-Process -Id $localProcessId
  }
  Remove-Item -LiteralPath $marker
 }
}
function Stop-G3 {
 Stop-Local 'g4-flow' 'services.core.g4.worker'
 Stop-Local 'g4-ai' 'services.core.g4.worker'
 Stop-Local 'g3-worker-0' 'services.core.g3.worker'
 Stop-Local 'g3-worker-1' 'services.core.g3.worker'
 Stop-Local 'sim-b' 'services.channel_simulator_b.main:create_app'
 & .\infra\scripts\g2.ps1 stop
}
function Start-Sims {
 Start-Local 'sim' @('uvicorn','services.channel_simulator.main:create_app','--factory','--host','127.0.0.1','--port','8001')
 Start-Local 'sim-b' @('uvicorn','services.channel_simulator_b.main:create_app','--factory','--host','127.0.0.1','--port','8002')
 Invoke-Python -m infra.g3_health sims
}
switch($Action){
 {$_ -in 'setup','prepare'}{
  Invoke-Python -m infra.local_postgres start
  Invoke-Python -m infra.g3_setup
  npm run build --prefix apps/admin
  if($LASTEXITCODE -ne 0){throw 'Frontend build failed'}
 }
 'stop'{Stop-G3}
 'start'{
  if(-not $env:G4_AI_ENABLED){$env:G4_AI_ENABLED='true'}
  Invoke-Python -m infra.local_postgres start
  $old=$env:G3_FAULTS_ENABLED;$oldLegacy=$env:SIM_FAULTS_ENABLED
  try{$env:G3_FAULTS_ENABLED='false';$env:SIM_FAULTS_ENABLED='false';Start-Sims}
  finally{$env:G3_FAULTS_ENABLED=$old;$env:SIM_FAULTS_ENABLED=$oldLegacy}
  Start-Local 'api' @('uvicorn','services.core.main:create_app','--factory','--host','127.0.0.1','--port','8000')
  Start-Local 'g3-worker-0' @('services.core.g3.worker')
  Start-Local 'g3-worker-1' @('services.core.g3.worker')
  # G2 parse/control lanes remain useful; legacy PUBLISH lane is not started.
  Start-Local 'g2-worker-0' @('services.core.g2.worker','--lane','parse')
  Start-Local 'g2-worker-1' @('services.core.g2.worker','--lane','control')
  Start-Local 'g4-flow' @('services.core.g4.worker','--lane','flow')
  Start-Local 'g4-ai' @('services.core.g4.worker','--lane','ai')
  Invoke-Python -m infra.g4_health all
 }
 'status'{Invoke-Python -m infra.g4_health all}
 {$_ -in 'test','contract-test','fault-test'}{
  $resume=(Test-Path -LiteralPath '.runtime\api.pid')
  Stop-G3
  $priorMode=$env:HOTEL_G1_TEST_MODE;$priorProfile=$env:HOTEL_TEST_PROFILE;$priorFault=$env:G3_FAULTS_ENABLED;$priorLegacyFault=$env:SIM_FAULTS_ENABLED
  try{
   $env:HOTEL_TEST_PROFILE='g4';Invoke-Python -m infra.prepare_tests
   $env:HOTEL_G1_TEST_MODE='true';$env:G3_FAULTS_ENABLED='true';$env:SIM_FAULTS_ENABLED='true'
   Start-Sims
   if($env:G4_TEST_TARGET){Invoke-Python -m pytest -q --tb=short $env:G4_TEST_TARGET --junitxml=.runtime/g4-dev-tests.xml}
   else{Invoke-Python -m pytest -q --tb=short --junitxml=docs/acceptance/g4-tests.xml}
  }finally{
   Stop-G3
   $env:HOTEL_G1_TEST_MODE=$priorMode;$env:HOTEL_TEST_PROFILE=$priorProfile;$env:G3_FAULTS_ENABLED=$priorFault;$env:SIM_FAULTS_ENABLED=$priorLegacyFault
   if($resume){& .\infra\scripts\g4.ps1 start}else{Start-Sims}
  }
 }
 'demo'{Invoke-Python -m infra.g4_smoke}
 'eval'{Invoke-Python -m infra.g4_eval}
 'prepare-model'{Invoke-Python -m infra.g4_model_prepare}
}
