$ErrorActionPreference = 'Stop'
$invoiceState = Join-Path $env:LOCALAPPDATA 'InvoiceDesk'
foreach ($service in @('api','worker')) {
    $pidFile = Join-Path $invoiceState "$service.pid"
    if (Test-Path $pidFile) {
        $servicePid = [int](Get-Content $pidFile)
        $record = Get-CimInstance Win32_Process -Filter "ProcessId = $servicePid" -ErrorAction SilentlyContinue
        if ($record -and $record.CommandLine.Contains($PSScriptRoot) -and ($record.CommandLine -match 'app.main:app|app.worker')) {
            & taskkill.exe /PID $servicePid /T /F | Out-Null
        }
    }
}
Write-Host 'InvoiceDesk processes stopped. Shared PostgreSQL remains running.'
