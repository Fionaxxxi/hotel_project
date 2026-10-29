param([ValidateSet('setup','start','stop','test','status')][string]$Action='start')
$ErrorActionPreference='Stop'
$ProjectRoot=(Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ProjectPython='D:\miniconda3\envs\hotel_project\python.exe'
Set-Location -LiteralPath $ProjectRoot
function Invoke-ProjectPython {
    & $ProjectPython -s @args
    if($LASTEXITCODE -ne 0){throw "Project command failed (exit $LASTEXITCODE)"}
}
function Stop-ProjectProcess([string]$Name,[string]$Module) {
    $pidFile=Join-Path $ProjectRoot ".runtime\$Name.pid"
    if(Test-Path -LiteralPath $pidFile){
        $savedProcessId=[int](Get-Content -LiteralPath $pidFile)
        $processInfo=Get-CimInstance Win32_Process -Filter "ProcessId=$savedProcessId"
        if($processInfo){
            if($processInfo.ExecutablePath -ne $ProjectPython -or -not $processInfo.CommandLine.Contains($Module)){
                throw "PID $savedProcessId does not match project module $Module; refusing to stop"
            }
            Stop-Process -Id $savedProcessId -ErrorAction Stop
        }
        Remove-Item -LiteralPath $pidFile
    }
}
function Start-ProjectProcess([string]$Name,[string]$Module,[string[]]$ExtraArgs) {
    $pidFile=Join-Path $ProjectRoot ".runtime\$Name.pid"
    if(Test-Path -LiteralPath $pidFile){throw "Existing $Name PID marker; stop the verified project process first"}
    $projectProcess=Start-Process -FilePath $ProjectPython -ArgumentList (@('-s','-m',$Module)+$ExtraArgs) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $ProjectRoot ".runtime\$Name.stdout.log") -RedirectStandardError (Join-Path $ProjectRoot ".runtime\$Name.stderr.log") -PassThru
    $projectProcess.Id | Set-Content -LiteralPath $pidFile
    Write-Output "$Name PID $($projectProcess.Id)"
}
function Start-Sim([bool]$Faults){
    $priorFaultValue=$env:SIM_FAULTS_ENABLED
    try {
        $env:SIM_FAULTS_ENABLED=if($Faults){'true'}else{'false'}
        Start-ProjectProcess 'sim' 'uvicorn' @('services.channel_simulator.main:create_app','--factory','--host','127.0.0.1','--port','8001')
    } finally { $env:SIM_FAULTS_ENABLED=$priorFaultValue }
    Invoke-ProjectPython -m infra.wait_services sim
}
switch($Action){
 'setup' {
    & .\infra\scripts\hotel.ps1 setup
    Invoke-ProjectPython -m infra.g1_setup
    npm ci --prefix apps/admin
    if($LASTEXITCODE -ne 0){throw 'Frontend install failed'}
    npm run build --prefix apps/admin
    if($LASTEXITCODE -ne 0){throw 'Frontend build failed'}
    Invoke-ProjectPython -m infra.export_openapi
 }
 'stop' {
    Stop-ProjectProcess 'dispatcher' 'services.core.workers.dispatch'
    Stop-ProjectProcess 'api' 'services.core.main:create_app'
    Stop-ProjectProcess 'sim' 'services.channel_simulator.main:create_app'
 }
 'start' {
    Invoke-ProjectPython -m infra.local_postgres start
    Start-Sim $false
    Start-ProjectProcess 'api' 'uvicorn' @('services.core.main:create_app','--factory','--host','127.0.0.1','--port','8000')
    Invoke-ProjectPython -m infra.wait_services core
    Start-ProjectProcess 'dispatcher' 'services.core.workers.dispatch' @()
 }
 'test' {
    $resumeDispatcher=Test-Path -LiteralPath (Join-Path $ProjectRoot '.runtime\dispatcher.pid')
    Stop-ProjectProcess 'dispatcher' 'services.core.workers.dispatch'
    Stop-ProjectProcess 'sim' 'services.channel_simulator.main:create_app'
    $previousTestMode=$env:HOTEL_G1_TEST_MODE
    try {
      Invoke-ProjectPython -m infra.prepare_tests
      $env:HOTEL_G1_TEST_MODE='true'
      Start-Sim $true
      $reportFile=if($env:HOTEL_TEST_PROFILE -eq 'g2'){'docs/acceptance/g2-tests.xml'}else{'docs/acceptance/g1-tests.xml'}
      $testOptions=@()
      $g3Tests=Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'tests') -Filter 'test_g*.py' | Where-Object { $_.Name -like 'test_g3*' -or $_.Name -like 'test_g4*' }
      foreach($item in $g3Tests){$testOptions+=('--ignore=tests/'+$item.Name)}
      if($env:HOTEL_TEST_PROFILE -ne 'g2'){
        $testOptions+=@('--ignore=tests/test_g2.py','--ignore=tests/test_g2_faults.py','--ignore=tests/test_g2_guards.py','--ignore=tests/test_g2_boundaries.py')
      }
      Invoke-ProjectPython -m pytest -q --tb=short "--junitxml=$reportFile" @testOptions
    } finally {
      Stop-ProjectProcess 'sim' 'services.channel_simulator.main:create_app'
      $env:HOTEL_G1_TEST_MODE=$previousTestMode
      Start-Sim $false
      if($resumeDispatcher){Start-ProjectProcess 'dispatcher' 'services.core.workers.dispatch' @()}
    }
 }
 'status' {Invoke-ProjectPython -m infra.wait_services all}
}
