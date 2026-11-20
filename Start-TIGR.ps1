$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$privateDir = Join-Path $PSScriptRoot '.deployment'
if ((Test-Path (Join-Path $privateDir 'mysql.ini')) -and -not (Get-NetTCPConnection -LocalPort 3306 -State Listen -ErrorAction SilentlyContinue)) {
    $mysql = Start-Process -FilePath 'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqld.exe' -ArgumentList ('--defaults-file="' + (Join-Path $privateDir 'mysql.ini') + '"') -WindowStyle Hidden -PassThru
    @{pid=$mysql.Id} | ConvertTo-Json | Set-Content (Join-Path $privateDir 'mysql-process.json')
}
$pgctl = Join-Path $privateDir 'postgresql17\pgsql\bin\pg_ctl.exe'
& $pgctl -D (Join-Path $privateDir 'pgdata') status *> $null
if ($LASTEXITCODE -ne 0) {
    & $pgctl -D (Join-Path $privateDir 'pgdata') -l (Join-Path $privateDir 'postgresql.log') -w start
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL failed to start.' }
}
$python = Join-Path $PSScriptRoot '.venv-win\Scripts\python.exe'
$env:PYTHONPATH = Join-Path $PSScriptRoot 'backend'
$processes = @{}
foreach ($service in @(
    @{ Name='api'; File=$python; Args=@('-m','uvicorn','tigr.main:app','--app-dir','backend','--host','127.0.0.1','--port','8001'); Port=8001 },
    @{ Name='worker'; File=$python; Args=@('-m','tigr.outbox_worker'); Port=0 },
    @{ Name='frontend'; File='C:\Program Files\nodejs\node.exe'; Args=@('frontend/node_modules/vite/bin/vite.js','frontend','--host','127.0.0.1','--port','5174','--strictPort'); Port=5174 }
)) {
    if ($service.Port -and (Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue)) {
        throw "Port $($service.Port) already in use; inspect before starting."
    }
    $process = Start-Process -FilePath $service.File -ArgumentList $service.Args -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $privateDir "$($service.Name).stdout.log") -RedirectStandardError (Join-Path $privateDir "$($service.Name).stderr.log")
    $processes[$service.Name] = $process.Id
}
$processes | ConvertTo-Json | Set-Content (Join-Path $privateDir 'processes.json')
Write-Output 'TIGR launched. Frontend: http://127.0.0.1:5174 API: http://127.0.0.1:8001'
