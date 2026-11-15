$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$invoiceState = Join-Path $env:LOCALAPPDATA 'InvoiceDesk'
$invoiceConfig = Get-Content (Join-Path $invoiceState 'config.json') -Raw | ConvertFrom-Json
$pgctl = Join-Path $invoiceConfig.bin 'pg_ctl.exe'
& $pgctl -D $invoiceConfig.cluster_data status *> $null
if ($LASTEXITCODE -ne 0) { & $pgctl -D $invoiceConfig.cluster_data -l (Join-Path $invoiceState 'postgres.log') -w start }
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (Get-NetTCPConnection -LocalPort 8030 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host 'Port 8030 is already in use. Existing service retained.'
} else {
    $api = Start-Process $python -ArgumentList '-m uvicorn app.main:app --host 127.0.0.1 --port 8030 --no-proxy-headers' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $invoiceState 'api.log') -RedirectStandardError (Join-Path $invoiceState 'api-error.log')
    $api.Id | Set-Content (Join-Path $invoiceState 'api.pid')
}
$workerRunning = $false
$workerPidFile = Join-Path $invoiceState 'worker.pid'
if (Test-Path $workerPidFile) {
    $workerRecord = Get-CimInstance Win32_Process -Filter "ProcessId = $(Get-Content $workerPidFile)" -ErrorAction SilentlyContinue
    $workerRunning = $workerRecord -and $workerRecord.CommandLine -like '*app.worker*'
}
if (-not $workerRunning) {
    $worker = Start-Process $python -ArgumentList '-m app.worker' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $invoiceState 'worker.log') -RedirectStandardError (Join-Path $invoiceState 'worker-error.log')
    $worker.Id | Set-Content $workerPidFile
}
Write-Host 'InvoiceDesk: http://127.0.0.1:8030'
