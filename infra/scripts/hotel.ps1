param(
    [ValidateSet('setup','start','stop-db','test','check','migrate','seed','contracts')]
    [string]$Action = 'start'
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ProjectEnv = 'D:\miniconda3\envs\hotel_project'
$Python = Join-Path $ProjectEnv 'python.exe'
Set-Location -LiteralPath $ProjectRoot
function Invoke-Python {
    & $Python -s @args
    if ($LASTEXITCODE -ne 0) { throw "Project command failed (exit $LASTEXITCODE)" }
}
switch ($Action) {
    'setup' {
        if (-not (Test-Path -LiteralPath $Python)) {
            & 'D:\miniconda3\python.exe' -m conda env create --prefix $ProjectEnv --file environment.yml
            if ($LASTEXITCODE -ne 0) { throw 'Conda environment creation failed' }
        }
        Invoke-Python -m pip install -r requirements.lock.txt
        Invoke-Python -m infra.generate_assets
        Invoke-Python -m infra.local_postgres start
        Invoke-Python -m infra.manage provision
        Invoke-Python -m infra.manage migrate
        Invoke-Python -m infra.manage seed
        Invoke-Python -m infra.export_openapi
    }
    'start' {
        Invoke-Python -m infra.local_postgres start
        Invoke-Python -m uvicorn services.core.main:create_app --factory --host 127.0.0.1 --port 8000 --log-level info
    }
    'stop-db' { Invoke-Python -m infra.local_postgres stop }
    'test' { & .\infra\scripts\g1.ps1 test }
    'check' {
        Invoke-Python -m ruff check .
        Invoke-Python -m ruff format --check .
    }
    'migrate' { Invoke-Python -m infra.manage migrate }
    'seed' { Invoke-Python -m infra.manage seed }
    'contracts' { Invoke-Python -m services.core.file_contract datasets/fixtures/F00-valid }
}
