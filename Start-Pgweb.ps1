$ErrorActionPreference = 'Stop'
$installDir = Join-Path $PSScriptRoot '.deployment\pgweb'
$exe = Join-Path $installDir 'pgweb.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "pgweb is not installed at $exe" }
if (Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue) {
    Write-Output 'Port 8081 is already in use. Check http://127.0.0.1:8081'
    return
}
$process = Start-Process -FilePath $exe -ArgumentList @('--bind=127.0.0.1', '--listen=8081', '--sessions', '--skip-open') -WorkingDirectory $installDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $installDir 'stdout.log') -RedirectStandardError (Join-Path $installDir 'stderr.log')
@{ pid = $process.Id } | ConvertTo-Json | Set-Content (Join-Path $installDir 'process.json')
Write-Output 'pgweb launched: http://127.0.0.1:8081'
