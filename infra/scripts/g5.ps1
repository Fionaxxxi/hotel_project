param([ValidateSet('prepare','setup','start','stop','status','test','contract-test','fault-test','demo','eval','prepare-model','security-test','backup','benchmark','evidence','parse-benchmark','protocol-benchmark','load-test','soak','verify-backup','restore','reconcile','release','export-contract')][string]$Action='start',[string]$BackupPath,[string]$RunId,[int]$DurationSeconds=3600)
$ErrorActionPreference='Stop'
$ProjectRoot=(Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ProjectPython='D:\miniconda3\envs\hotel_project\python.exe'
Set-Location -LiteralPath $ProjectRoot
function Invoke-Python { & $ProjectPython -s @args; if($LASTEXITCODE -ne 0){throw "Project command failed ($LASTEXITCODE)"} }
function Start-Local([string]$Name,[string[]]$Arguments){
 $marker=Join-Path $ProjectRoot ".runtime\$Name.pid"
 if(Test-Path -LiteralPath $marker){throw "Existing $Name marker; stop project services first"}
 $priorSlot=$env:HOTEL_WORKER_SLOT
 $env:HOTEL_WORKER_SLOT=$Name
 try{
 $service=Start-Process -FilePath $ProjectPython -ArgumentList (@('-s','-m')+$Arguments) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $ProjectRoot ".runtime\$Name.stdout.log") -RedirectStandardError (Join-Path $ProjectRoot ".runtime\$Name.stderr.log") -PassThru
 }finally{$env:HOTEL_WORKER_SLOT=$priorSlot}
 $service.Id | Set-Content -LiteralPath $marker
 Write-Output "$Name PID $($service.Id)"
}
function Stop-Local([string]$Name,[string]$Module){
 $marker=Join-Path $ProjectRoot ".runtime\$Name.pid"
 if(Test-Path -LiteralPath $marker){
  $localProcessId=[int](Get-Content -LiteralPath $marker)
  Invoke-Python -m infra.g5_process --pid $localProcessId --module $Module
  Remove-Item -LiteralPath $marker
 }
}
function Stop-G3 {
 Stop-Local 'g5-ops' 'services.core.g5.worker'
 Stop-Local 'g4-flow' 'services.core.g4.worker'
 Stop-Local 'g4-ai' 'services.core.g4.worker'
 Stop-Local 'g3-worker-0' 'services.core.g3.worker'
 Stop-Local 'g3-worker-1' 'services.core.g3.worker'
 Stop-Local 'sim-b' 'services.channel_simulator_b.main:create_app'
 Stop-Local 'g2-worker-0' 'services.core.g2.worker'
 Stop-Local 'g2-worker-1' 'services.core.g2.worker'
 Stop-Local 'g2-worker-2' 'services.core.g2.worker'
 Stop-Local 'dispatcher' 'services.core.workers.dispatch'
 Stop-Local 'api' 'services.core.main:create_app'
 Stop-Local 'sim' 'services.channel_simulator.main:create_app'
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
  Invoke-Python -m infra.g5_setup
  npm run build --prefix apps/admin
  if($LASTEXITCODE -ne 0){throw 'Frontend build failed'}
 }
 'stop'{Stop-G3}
 'start'{
  $env:G5_OFFLINE_ONLY='true'
  $env:G5_ISOLATED_SIMULATORS='false'
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
  @{ai_enabled=($env:G4_AI_ENABLED -eq 'true');offline_only=$true;simulator_profile='normal'} | ConvertTo-Json | Set-Content -LiteralPath '.runtime\g5-service-profile.json'
  Start-Local 'g5-ops' @('services.core.g5.worker')
  Invoke-Python -m infra.g5_health all
 }
 'status'{Invoke-Python -m infra.g5_health all}
 {$_ -in 'test','contract-test','fault-test','security-test'}{
  $resume=(Test-Path -LiteralPath '.runtime\api.pid')
  Stop-G3
  $priorMode=$env:HOTEL_G1_TEST_MODE;$priorProfile=$env:HOTEL_TEST_PROFILE;$priorFault=$env:G3_FAULTS_ENABLED;$priorLegacyFault=$env:SIM_FAULTS_ENABLED
  try{
   $env:HOTEL_TEST_PROFILE='g5';Invoke-Python -m infra.prepare_tests
   $env:HOTEL_G1_TEST_MODE='true';$env:G3_FAULTS_ENABLED='true';$env:SIM_FAULTS_ENABLED='true'
   Start-Sims
   if($env:G5_TEST_TARGET){Invoke-Python -m pytest -q --tb=short @($env:G5_TEST_TARGET -split ',') --junitxml=.runtime/g5-dev-tests.xml}
   elseif($Action -eq 'security-test'){Invoke-Python -m pytest -q --tb=short tests/test_api_security.py tests/test_database_security.py tests/test_g1_safety.py tests/test_g4_security.py tests/test_g5_security.py tests/test_g5_offline.py --junitxml=docs/acceptance/g5-security-tests.xml}
   else{Invoke-Python -m pytest -q --tb=short --junitxml=docs/acceptance/g5-tests.xml}
  }finally{
   Stop-G3
   $env:HOTEL_G1_TEST_MODE=$priorMode;$env:HOTEL_TEST_PROFILE=$priorProfile;$env:G3_FAULTS_ENABLED=$priorFault;$env:SIM_FAULTS_ENABLED=$priorLegacyFault
   if($resume){& .\infra\scripts\g5.ps1 start}else{Start-Sims}
  }
 }
 'backup'{
  $priorAi=$env:G4_AI_ENABLED
  if(Test-Path -LiteralPath '.runtime\g5-service-profile.json'){
   $profile=Get-Content -Raw -LiteralPath '.runtime\g5-service-profile.json' | ConvertFrom-Json
   $env:G4_AI_ENABLED=([string]$profile.ai_enabled).ToLowerInvariant()
  }
  $resume=(Test-Path -LiteralPath '.runtime\api.pid')
  Stop-G3
  try{Invoke-Python -m infra.g5_recovery backup}finally{try{if($resume){& .\infra\scripts\g5.ps1 start}}finally{$env:G4_AI_ENABLED=$priorAi}}
 }
 'benchmark'{Invoke-Python -m infra.g5_benchmark}
 'parse-benchmark'{
  $prior=$env:HOTEL_TEST_PROFILE
  try{$env:HOTEL_TEST_PROFILE='g5_benchmark';Invoke-Python -m infra.prepare_tests;Invoke-Python -m infra.g5_parse_benchmark}finally{$env:HOTEL_TEST_PROFILE=$prior}
 }
 'protocol-benchmark'{Invoke-Python -m infra.g5_protocol_benchmark}
 'load-test'{Invoke-Python -m infra.g5_load}
 'soak'{Invoke-Python -m infra.g5_soak --seconds $DurationSeconds}
 'verify-backup'{Invoke-Python -m infra.g5_recovery verify --path $BackupPath}
 'restore'{Invoke-Python -m infra.g5_recovery restore --path $BackupPath --run-id $RunId}
 'reconcile'{Invoke-Python -m infra.g5_reconcile_restore --run-id $RunId}
 'release'{Invoke-Python -m infra.g5_release}
 'export-contract'{Invoke-Python -m infra.g5_openapi}
 'evidence'{Invoke-Python -m infra.g5_evidence}
 'demo'{Invoke-Python -m infra.g5_demo}
 'eval'{Invoke-Python -m infra.g4_eval}
 'prepare-model'{Invoke-Python -m infra.g4_model_prepare}
}
